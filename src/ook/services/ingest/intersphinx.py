"""Ingest service for Sphinx object inventories.

Ook pulls documentation links rather than being pushed them: a registered
site's ``objects.inv`` is fetched on a schedule, parsed, and turned into
entities and links. This module is that run.

The unit of work is one source, not one run. `IntersphinxIngestService`
exposes it as `~IntersphinxIngestService.ingest_source`, commits it on its
own, and both triggers -- the ``ook ingest-intersphinx`` sweep and the
``POST /ingest/intersphinx`` endpoint -- are loops or single calls over
that one method, as a Kafka consumer reacting to a published site would be.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from urllib.parse import urljoin

from sqlalchemy.ext.asyncio import AsyncSession
from structlog.stdlib import BoundLogger

from ook.domain.intersphinx import InventoryCacheStatus
from ook.domain.intersphinxentities import (
    PYTHON_SPHINX_DOMAIN,
    IntersphinxSourceLink,
    InventoryEntity,
    build_entities,
    parse_inventory,
)
from ook.domain.intersphinxsources import IntersphinxSource, SourceIngestStatus
from ook.exceptions import (
    InvalidInventoryUrlError,
    InventoryParseError,
    NotFoundError,
    UpstreamInventoryError,
)
from ook.services.intersphinx import IntersphinxCacheService, RevalidationMode
from ook.storage.intersphinxentitystore import IntersphinxEntityStore
from ook.storage.intersphinxsourcestore import IntersphinxSourceStore

__all__ = [
    "SPHINX_DOMAIN_LINK_TYPES",
    "IntersphinxIngestService",
    "IntersphinxIngestSummary",
    "SourceIngestResult",
]


SPHINX_DOMAIN_LINK_TYPES: Mapping[str, str] = MappingProxyType(
    {PYTHON_SPHINX_DOMAIN: "python_api"}
)
"""The kind of documentation a link into each Sphinx domain points at.

Keyed by Sphinx domain and not by anything finer, because the Links API's
``type`` answers "what sort of documentation is this?" -- which a Python
object's ``class`` and ``method`` roles answer identically. Its keys must
match `~ook.domain.intersphinxentities.SPHINX_DOMAIN_HIERARCHIES`: a domain
Ook can place in a hierarchy but cannot describe a link for is a domain it
cannot ingest.
"""


_MAX_ERROR_LENGTH = 1000
"""How much of a failure's description is kept on the registry row.

Enough to identify the failure at a glance in the sources API; not so much
that an upstream error page's text becomes the row.
"""


_INGEST_FAILURES = (
    UpstreamInventoryError,
    InvalidInventoryUrlError,
    InventoryParseError,
)
"""The failures one source may have without stopping the run.

Everything a *site* can do wrong: unreachable, refused by the SSRF guard,
or serving bytes that are not an inventory. A database error is not in the
list -- that is Ook's own failure, it is not per-source, and recording it
on one registry row while the run continues would hide it.
"""


def _unregistered_url_message(url: str) -> str:
    """Describe an inventory URL the registry does not hold."""
    return (
        f"No documentation source is registered with the inventory URL "
        f"{url!r}."
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceIngestResult:
    """The outcome of ingesting one documentation source."""

    source_id: int
    """The database ID of the source that was ingested."""

    url: str
    """The inventory URL that was ingested."""

    title: str
    """The human title of the documentation site."""

    status: SourceIngestStatus
    """Whether the source's links were replaced or the attempt failed."""

    entity_count: int
    """The number of entities the inventory contributed.

    Zero on a failure, which is not the same as a site that documents
    nothing: `status` is what distinguishes them.
    """

    link_count: int
    """The number of links written for this source."""

    pruned_count: int
    """The number of entities pruned after this source's links changed.

    Attributed to the source whose replace exposed them, though pruning
    itself is global: an entity is pruned when *no* source documents it or
    anything below it.
    """

    error: str | None
    """A description of the failure, or None when the ingest succeeded."""

    cache_status: InventoryCacheStatus | None
    """How fresh the inventory this source was ingested from was.

    `~ook.domain.intersphinx.InventoryCacheStatus.hit` when the copy parsed
    was one upstream had just confirmed (or just sent),
    `~ook.domain.intersphinx.InventoryCacheStatus.miss` when this run was
    the site's first, and
    `~ook.domain.intersphinx.InventoryCacheStatus.stale` when the origin
    could not be reached and the links were rebuilt from the copy Ook
    already held -- which is a successful ingest of a copy that may no
    longer describe the site, and the one outcome an operator has to be
    told about. None on a failure, which never got an inventory at all.
    """


@dataclass(frozen=True, slots=True)
class IntersphinxIngestSummary:
    """The outcome of an ingest run over one or more sources."""

    results: list[SourceIngestResult] = field(default_factory=list)
    """Each visited source's outcome, in the order they were visited."""

    @property
    def succeeded(self) -> int:
        """The number of sources whose links were replaced."""
        return sum(
            1
            for result in self.results
            if result.status is SourceIngestStatus.success
        )

    @property
    def failed(self) -> int:
        """The number of sources whose fetch or parse failed."""
        return sum(
            1
            for result in self.results
            if result.status is SourceIngestStatus.failure
        )

    @property
    def stale_count(self) -> int:
        """The number of sources ingested from an unrevalidated copy.

        Counted apart from `failed` because these sources *were* ingested:
        their links are current with the inventory Ook holds, just not with
        the site, whose origin could not be reached to check.
        """
        return sum(
            1
            for result in self.results
            if result.cache_status is InventoryCacheStatus.stale
        )

    @property
    def entity_count(self) -> int:
        """The number of entities the run's inventories contributed."""
        return sum(result.entity_count for result in self.results)

    @property
    def link_count(self) -> int:
        """The number of links the run wrote."""
        return sum(result.link_count for result in self.results)

    @property
    def pruned_count(self) -> int:
        """The number of entities the run pruned."""
        return sum(result.pruned_count for result in self.results)


@dataclass(frozen=True, slots=True, kw_only=True)
class _ParsedInventory:
    """One site's inventory, fetched and parsed but not yet stored.

    An ingest is split into this half and the write that follows it because
    the registration lock that serializes concurrent ingests belongs between
    them: everything that talks to the site happens before the lock is
    taken, and everything that writes happens under it.
    """

    url: str
    """The inventory URL these entities were read from.

    Carried alongside them because every one of their links is resolved
    against this URL's directory, and the registration it came from is free
    to name a different one by the time the links are written.
    """

    entities: list[InventoryEntity]
    """The entities the inventory declares, in the order it declares them."""

    cache_status: InventoryCacheStatus
    """How fresh the inventory these entities were parsed from was."""


@dataclass(frozen=True, slots=True, kw_only=True)
class _ReplacedLinks:
    """What one site's link replacement wrote, and what it was built from.

    Three values that only mean anything together: the counts describe the
    replace, and `cache_status` describes the inventory the replace was
    derived from, which is what tells an operator whether those counts
    describe the site or the last copy of it Ook could reach.
    """

    entity_count: int
    """The number of entities stored for this site."""

    link_count: int
    """The number of links written for this site."""

    cache_status: InventoryCacheStatus
    """How fresh the inventory those links were built from was."""


class IntersphinxIngestService:
    """Ingests registered documentation sites' Sphinx object inventories.

    Unlike the stores it drives, this service owns its transaction
    boundaries and must be called without a surrounding transaction: each
    source's links are replaced and committed on their own, so a site whose
    inventory cannot be read leaves every other site's ingest committed, and
    a crash mid-run keeps the sources already done.

    Freshness is this service's own business, not the inventory cache's
    refresh CronJob's. Every inventory is pulled through the cache with a
    `~ook.services.intersphinx.RevalidationMode` that revalidates it against
    its origin when it matters -- past the freshness TTL for a sweep,
    unconditionally for a named source -- so what a run parses is what the
    sites publish, whether or not ``ook refresh-intersphinx`` has run. That
    job warms the client-facing inventory cache; the two have no ordering
    between them.

    Parameters
    ----------
    cache_service
        The intersphinx inventory cache every inventory is fetched through.
    entity_store
        The store of entities and their links.
    source_store
        The registry of documentation sources.
    session
        The database session, whose transactions this service commits.
    logger
        The logger.
    """

    def __init__(
        self,
        *,
        cache_service: IntersphinxCacheService,
        entity_store: IntersphinxEntityStore,
        source_store: IntersphinxSourceStore,
        session: AsyncSession,
        logger: BoundLogger,
    ) -> None:
        self._cache_service = cache_service
        self._entity_store = entity_store
        self._source_store = source_store
        self._session = session
        self._logger = logger

    async def ingest_sources(self) -> IntersphinxIngestSummary:
        """Ingest every enabled documentation source in turn.

        A source that fails is recorded on its registry row and the run
        continues with the rest, so one unreachable site does not cost the
        others their refresh. The summary reports each source's outcome; it
        is the caller's business (the CLI's, so a CronJob shows red) to
        decide what a failure count means.

        Each source's inventory is revalidated when it has aged past the
        cache's freshness TTL, so a sweep parses what its sites publish now
        without depending on the cache-warming refresh job having run first.
        A site inside the TTL costs no upstream request, and one whose
        origin will not answer is ingested from the copy Ook holds and
        reported as `~ook.domain.intersphinx.InventoryCacheStatus.stale`.

        Returns
        -------
        IntersphinxIngestSummary
            Each enabled source's outcome, in the order they were visited.
            A source deregistered mid-run is absent rather than reported:
            see `ingest_source`.
        """
        sources = await self._source_store.list_sources(enabled_only=True)
        # Commit the registry read as its own short transaction: no snapshot
        # should be held open across the per-source inventory fetches below.
        await self._session.commit()

        results = []
        for source in sources:
            result = await self.ingest_source(
                source, revalidate=RevalidationMode.when_stale
            )
            # A source deregistered while this run was fetching its inventory
            # has no outcome to report: it is not a site that failed, it is a
            # site the run no longer has anything to say about.
            if result is not None:
                results.append(result)
        summary = IntersphinxIngestSummary(results=results)
        self._logger.info(
            "Completed intersphinx ingest",
            considered=len(results),
            succeeded=summary.succeeded,
            failed=summary.failed,
            stale_count=summary.stale_count,
            entity_count=summary.entity_count,
            link_count=summary.link_count,
            pruned_count=summary.pruned_count,
        )
        return summary

    async def ingest_source_url(self, url: str) -> SourceIngestResult:
        """Ingest the one registered source with this inventory URL.

        The source is ingested whatever its ``enabled`` flag says. That flag
        governs which sites a *sweep* visits, and naming one is the more
        specific instruction -- it is also the only way to try a
        registration out before turning it on.

        Its inventory is revalidated however fresh the cached copy is, for
        the same reason: an operator naming one source has been told the
        site republished, or is checking what it publishes, and either way
        wants this run to look. It costs one conditional request.

        Parameters
        ----------
        url
            The registered source's inventory URL.

        Returns
        -------
        SourceIngestResult
            The source's outcome.

        Raises
        ------
        NotFoundError
            Raised if no source is registered with that inventory URL. An
            unregistered inventory is not something to ingest: the
            registration is what supplies the site's title and the identity
            its links are replaced against. Raised for the same reason when
            the registration is deleted while its inventory is being
            fetched -- by the time there was anything to write, the URL
            named nothing.
        """
        source = await self._source_store.get_source_by_url(url)
        await self._session.commit()
        if source is None:
            raise NotFoundError(message=_unregistered_url_message(url))
        result = await self.ingest_source(
            source, revalidate=RevalidationMode.always
        )
        if result is None:
            raise NotFoundError(message=_unregistered_url_message(url))
        return result

    async def ingest_source(
        self,
        source: IntersphinxSource,
        *,
        revalidate: RevalidationMode = RevalidationMode.when_stale,
    ) -> SourceIngestResult | None:
        """Ingest one documentation source and commit the outcome.

        The whole of one site's ingest -- its entities, the replacement of
        its links, and the pruning that replacement exposes -- is one
        transaction, so a reader never sees the site's links half replaced.
        The registry stamp is committed with it, which is what makes
        ``last_status`` a description of the links actually stored.

        The inventory's own revalidation is deliberately *not* part of that
        transaction. It is committed on its own, before this method opens
        the one it writes links in, because it is an HTTP fetch: holding the
        row lock its bookkeeping takes across an origin that may spend the
        whole fetch budget is the invariant the cache service's docstrings
        keep, and it would be broken here rather than there. The split also
        matches what the two writes mean -- a revalidated inventory is a
        fact about the cache whether or not this site's links then replace
        cleanly -- and it is why nothing about this ingest is written until
        the inventory is in hand, so a fetch failure deletes no links.

        A fetch or parse failure is recorded and returned rather than
        raised: the site's previous links are kept (nothing was deleted
        before the inventory was in hand) and the caller decides what to do
        about it. The failure is committed rather than rolled back, because
        by then the cache has recorded upstream's refusal and that record is
        what keeps a broken origin from being hammered on the next run.

        Concurrent ingests of *one* source are serialized on that source's
        registration row, locked with a ``SELECT ... FOR UPDATE`` once the
        inventory is in hand. Without it the two replaces interleave: under
        ``READ COMMITTED`` the second transaction's ``DELETE`` is judged by
        a snapshot taken before the first's inserts committed, so it deletes
        the rows the first already deleted, sees none of what the first
        wrote, and inserts a second copy of every link. Waiting on the lock
        is what fixes that, because a statement a waiter runs *after* the
        lock is granted takes a fresh snapshot: the delete then sees the
        first ingest's links and replaces them, which is all this needs one
        lock for. Sources lock separately, so a sweep is never serialized as
        a whole.

        The lock is deliberately taken *after* the inventory, never before:
        no row lock may be held across an origin that can spend the whole
        fetch budget, which is the invariant the cache service's docstrings
        keep. What follows the lock is decided from the row it re-read
        rather than the one this method was handed, which by then may be a
        registration another ingest -- or an operator -- has just rewritten.

        A source whose registration is deleted while its inventory is in
        flight ends as a no-op: the lock read finds no row, nothing is
        written, and None is returned. Writing links then would resurrect a
        site that had been deregistered, on a registration only the links'
        own foreign key still referred to.

        Parameters
        ----------
        source
            The registered source to ingest.
        revalidate
            How hard to work for a current inventory before parsing it.
            Defaults to
            `~ook.services.intersphinx.RevalidationMode.when_stale`, the
            sweep's mode; the ``source_url`` trigger asks for
            `~ook.services.intersphinx.RevalidationMode.always`.

        Returns
        -------
        SourceIngestResult or None
            The source's outcome, or None if the source was deregistered
            between its inventory being fetched and its links being written.
        """
        logger = self._logger.bind(source_id=source.id, url=source.url)
        try:
            parsed = await self._read_inventory(source, revalidate=revalidate)
        except _INGEST_FAILURES as exc:
            return await self._record_failure(source, exc, logger=logger)

        locked = await self._source_store.lock_source(source.id)
        if locked is None:
            # Committed rather than rolled back: the ingest wrote nothing of
            # its own, and what is pending is the cache's record of a fetch
            # that really did happen, which the deregistration of one site
            # is no reason to throw away.
            await self._session.commit()
            logger.info("Skipped intersphinx source deleted during its ingest")
            return None

        replaced = await self._store_links(locked, parsed)
        pruned_count = await self._entity_store.prune_orphan_entities()
        await self._source_store.record_ingest_outcome(
            locked.id,
            date_ingested=datetime.now(tz=UTC),
            status=SourceIngestStatus.success,
            error=None,
        )
        await self._session.commit()
        logger.info(
            "Ingested intersphinx source",
            entity_count=replaced.entity_count,
            link_count=replaced.link_count,
            pruned_count=pruned_count,
            cache_status=replaced.cache_status,
        )
        return SourceIngestResult(
            source_id=locked.id,
            # The URL that was actually fetched, which is the one the source
            # carried when the ingest started: a rename committed while the
            # inventory was in flight does not change where these links came
            # from, even though the title they carry does come from the
            # re-read row.
            url=source.url,
            title=locked.title,
            status=SourceIngestStatus.success,
            entity_count=replaced.entity_count,
            link_count=replaced.link_count,
            pruned_count=pruned_count,
            error=None,
            cache_status=replaced.cache_status,
        )

    async def _read_inventory(
        self, source: IntersphinxSource, *, revalidate: RevalidationMode
    ) -> _ParsedInventory:
        """Fetch and parse one site's inventory, writing nothing of Ook's.

        Everything that can reach the origin lives here, and nothing that
        writes an entity or a link does, so `ingest_source` can put the
        registration lock between the two.

        Returns
        -------
        _ParsedInventory
            The entities the inventory declares, and how fresh the copy
            they were parsed from was.
        """
        served = await self._cache_service.get_inventory(
            source.url, revalidate=revalidate
        )
        content = served.inventory.content
        if content is None:
            # The cache serves content or raises, so this is unreachable in
            # practice; reported as a parse failure rather than asserted
            # because an empty cache row is a fact about one site and must
            # not stop the run.
            raise InventoryParseError(
                f"The cached inventory for {source.url} holds no content."
            )

        return _ParsedInventory(
            url=source.url,
            entities=build_entities(parse_inventory(content)),
            cache_status=served.cache_status,
        )

    async def _store_links(
        self, source: IntersphinxSource, parsed: _ParsedInventory
    ) -> _ReplacedLinks:
        """Store one site's entities and replace its links.

        Called only with the source's registration row locked, which is what
        makes the replace's delete-then-insert safe against a concurrent
        ingest of the same site.

        Parameters
        ----------
        source
            The source as it stands under the lock, whose title every link
            written here carries.
        parsed
            The inventory `_read_inventory` fetched and parsed.

        Returns
        -------
        _ReplacedLinks
            What the replace wrote, and how fresh the inventory it was
            built from was.
        """
        entity_ids = await self._entity_store.upsert_entities(parsed.entities)
        links = self._build_links(parsed, entity_ids)
        link_count = await self._entity_store.replace_source_links(
            source.id, links, collection_title=source.title
        )
        return _ReplacedLinks(
            entity_count=len(entity_ids),
            link_count=link_count,
            cache_status=parsed.cache_status,
        )

    def _build_links(
        self,
        parsed: _ParsedInventory,
        entity_ids: dict[tuple[str, str], int],
    ) -> list[IntersphinxSourceLink]:
        """Turn one inventory's entities into the links the site provides.

        One link per entity, first declaration wins -- matching how
        `~ook.storage.intersphinxentitystore.IntersphinxEntityStore.upsert_entities`
        merges a name an inventory declares twice. Without that a site that
        declared a name under two roles would contribute two links to the
        same entity, which reads as two sites documenting it.
        """
        links: list[IntersphinxSourceLink] = []
        seen: set[tuple[str, str]] = set()
        for entity in parsed.entities:
            identity = (entity.sphinx_domain, entity.name)
            if identity in seen:
                continue
            seen.add(identity)
            links.append(
                IntersphinxSourceLink(
                    entity_id=entity_ids[identity],
                    # The inventory's URI is relative to the directory
                    # holding the inventory, which is what the inventory
                    # URL's own directory is.
                    html_url=urljoin(parsed.url, entity.uri),
                    title=entity.display_name,
                    type=SPHINX_DOMAIN_LINK_TYPES[entity.sphinx_domain],
                )
            )
        return links

    async def _record_failure(
        self,
        source: IntersphinxSource,
        error: Exception,
        *,
        logger: BoundLogger,
    ) -> SourceIngestResult:
        """Stamp a source's failed ingest on its registry row and commit."""
        detail = str(error)[:_MAX_ERROR_LENGTH] or type(error).__name__
        await self._source_store.record_ingest_outcome(
            source.id,
            date_ingested=datetime.now(tz=UTC),
            status=SourceIngestStatus.failure,
            error=detail,
        )
        await self._session.commit()
        logger.warning(
            "Failed to ingest intersphinx source",
            error=detail,
            error_type=type(error).__name__,
        )
        return SourceIngestResult(
            source_id=source.id,
            url=source.url,
            title=source.title,
            status=SourceIngestStatus.failure,
            entity_count=0,
            link_count=0,
            pruned_count=0,
            error=detail,
            # A failed ingest was served no inventory, so it has no
            # freshness to describe -- not even a stale one.
            cache_status=None,
        )
