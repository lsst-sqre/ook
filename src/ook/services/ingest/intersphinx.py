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

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from urllib.parse import urljoin

from sqlalchemy.exc import IntegrityError
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
list -- that is Ook's own failure rather than the site's, and recording it
here, where nothing has been written yet, would say the site failed when
Ook did. `_WRITE_FAILURES` covers the one database error that is genuinely
about one source.
"""


_WRITE_FAILURES = (IntegrityError,)
"""The database failures that cost one source its ingest and no more.

An integrity error from a source's write phase is a statement about that
source alone: the links it was inserting no longer agree with the rows they
point at, because something else changed the entity graph underneath. The
graph's own lock makes that vanishingly unlikely, and a failure here is
worth looking at -- but the run it happened in is a sweep over every other
site, and taking those down with it would turn one lost race into a fleet
with no refresh at all.

The rollback that has to precede the recording is why this is caught apart
from `_INGEST_FAILURES` rather than added to it: those are raised before
any write, on a transaction still fit to use.
"""


_WRITE_CONFLICT_MESSAGE = (
    "The database refused this site's links, so the links it already had"
    " are kept. Something else changed the entities they point at while"
    " they were being written; the next ingest run rewrites them."
)
"""What a source's registry row says about a refused write.

The database's own message names a statement and its bind parameters, which
tells an operator reading the sources API nothing they can act on and
crowds a thousand characters of the row. The full error goes to the log,
where it can be read against the rest of the run.
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

    unchanged: bool
    """Whether the site's inventory was recognized and nothing rewritten.

    True when the inventory hashed to the one the last successful ingest
    read, so the links already stored were built from exactly these bytes
    under exactly this registration and rewriting them would have replaced
    them with themselves. The ingest still succeeded and the registration is
    still stamped; the counts below are all zero because nothing was
    written, not because nothing was found. Always False on a failure.
    """

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
    itself is global: an entity is pruned when *no* source links to it.
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

    sweep_pruned_count: int = 0
    """The number of entities the run's own convergence pass deleted.

    Names half of what that pass does, and usually the smaller half: it
    recomputes every entity's containment before it prunes anything, and on
    a settled fleet the recompute is the whole of its work while this stays
    zero. The prune is what gets counted because a deletion is the one
    outcome a run has to attribute to itself -- a source that replaced its
    own links reports its own.

    See `~IntersphinxIngestService._converge_after_sweep` for why a run that
    replaced nothing converges at all.
    """

    @property
    def succeeded(self) -> int:
        """The number of sources whose ingest succeeded.

        Counts a source whose links were replaced and one whose inventory
        was recognized alike: both ended with the site's links current with
        what its inventory says. `unchanged_count` is what separates them.
        """
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
    def unchanged_count(self) -> int:
        """The number of sources whose inventory was already ingested.

        Counted apart from `succeeded`, which includes them: they are the
        cheap outcome a healthy scheduled sweep is mostly made of, and a run
        that replaced every site's links looks the same in every other
        count.
        """
        return sum(1 for result in self.results if result.unchanged)

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
        """The number of entities the run pruned, however it pruned them."""
        return (
            sum(result.pruned_count for result in self.results)
            + self.sweep_pruned_count
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class _FetchedInventory:
    """One site's inventory, in hand but not yet read.

    An ingest is split into this half and the write that follows it because
    the registration lock that serializes concurrent ingests belongs between
    them: everything that talks to the site happens before the lock is
    taken, and everything that writes -- and the parse whose result is only
    ever written -- happens under it.
    """

    url: str
    """The inventory URL these bytes were read from.

    Carried alongside them because every link parsed out of them is
    resolved against this URL's directory, and the registration it came
    from is free to be repointed while they are in flight. It is compared
    against the locked registration before anything is written, which is
    what keeps links resolved against a directory the site has moved off
    from being stored -- and stamped as current.
    """

    content: bytes
    """The raw ``objects.inv`` payload the cache served."""

    digest: str
    """The SHA-256 hex digest of `content`.

    Compared with the digest the source's last successful ingest recorded,
    which is what lets a run recognize an inventory it has already turned
    into links rather than rebuilding them from it.
    """

    cache_status: InventoryCacheStatus
    """How fresh the copy of the inventory these bytes came from was."""


@dataclass(frozen=True, slots=True, kw_only=True)
class _ParsedInventory:
    """One site's inventory, read into the entities it declares."""

    url: str
    """The inventory URL these entities were read from."""

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


def _is_already_ingested(
    source: IntersphinxSource, fetched: _FetchedInventory
) -> bool:
    """Report whether a source's stored links were built from these bytes.

    Three things have to hold, and each rules out a different way the
    stored links could disagree with the inventory just fetched. The
    digests must match, or the site republished. A digest must have been
    recorded at all, since the source store's ``update_source`` clears it
    when a registration is retitled or repointed -- the links carry the
    title as their ``collection_title``, so a rename leaves them
    describing a site that no longer exists under that name. And the last
    attempt must have *succeeded*: the digest survives a failure because a
    failed ingest deletes nothing, but the run after a failure is the one
    an operator is watching, and it should prove the site's recovery rather
    than assert it from bookkeeping.

    A fourth condition is the caller's rather than this function's: the
    registration must still name the URL *fetched* was read from. Bytes are
    identified here by their digest alone, and an ``objects.inv`` records
    its project and version but not the site it is published at -- so a
    registration repointed at a mirror serving byte-identical bytes would
    be recognized as unchanged and left with links resolved against the
    directory it moved off. `IntersphinxIngestService.ingest_source`
    abandons such an ingest before reaching here, so what this compares is
    always two readings of one URL.
    """
    return (
        source.last_status is SourceIngestStatus.success
        and source.ingested_content_digest is not None
        and source.ingested_content_digest == fetched.digest
    )


def _parse_fetched_inventory(fetched: _FetchedInventory) -> _ParsedInventory:
    """Read a fetched inventory into the entities it declares.

    Called under the source's registration lock rather than beside the
    fetch, because its only consumer is the write that follows it: an
    inventory `_is_already_ingested` recognizes is never parsed at all,
    which is most of what skipping an unchanged source saves. The parse is
    local work with no origin to wait on, so holding the lock across it
    costs nothing the lock was taken to protect.

    Raises
    ------
    InventoryParseError
        Raised if the payload is not a readable inventory.
    """
    return _ParsedInventory(
        url=fetched.url,
        entities=build_entities(parse_inventory(fetched.content)),
        cache_status=fetched.cache_status,
    )


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

        A site whose inventory hashes to the one its last successful ingest
        read is recognized rather than re-read, which is the ordinary
        outcome for a scheduled sweep and the reason the run also converges
        stored entities on its own account. That convergence normally rides
        on a source's replace, and a settled fleet performs none at all --
        so a change to how containment is *derived* would never reach the
        rows it describes, and an entity somehow left with no link would
        never be collected. This method therefore runs one final convergence
        pass, in a short transaction of its own, whenever no source in the
        run replaced its links. See `_converge_after_sweep`.

        Returns
        -------
        IntersphinxIngestSummary
            Each enabled source's outcome, in the order they were visited.
            A source deregistered or repointed mid-run is absent rather than
            reported: see `ingest_source`.
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
        summary = IntersphinxIngestSummary(
            results=results,
            sweep_pruned_count=await self._converge_after_sweep(results),
        )
        self._logger.info(
            "Completed intersphinx ingest",
            considered=len(results),
            succeeded=summary.succeeded,
            failed=summary.failed,
            unchanged_count=summary.unchanged_count,
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
            the registration is deleted, or repointed at some other
            inventory, while this one is being fetched -- by the time there
            was anything to write, the URL named nothing.
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

        The inventory's own serve is deliberately *not* part of that
        transaction. Whatever it wrote about the cache -- a cold miss's
        stored inventory, a revalidation's outcome, or just the bumped
        ``date_requested`` every serve stamps -- is committed by this method
        the moment the inventory is in hand, before the registration lock is
        waited on. The cache service commits only what it must (the bump
        ahead of a conditional GET, so no row lock outlives an HTTP fetch)
        and leaves the rest of the boundary to its caller, which is here.
        The split matches what the two writes mean -- what the cache
        recorded is a fact about the cache whether or not this site's links
        then replace cleanly -- and it is what keeps everything else that
        touches that row, the public ``GET`` and the refresh job included,
        from queueing behind a registration lock and an entity-graph write
        it has nothing to do with. It is also why nothing about this ingest
        is written until the inventory is in hand, so a fetch failure
        deletes no links.

        A fetch or parse failure is recorded and returned rather than
        raised: the site's previous links are kept (nothing was deleted
        before the inventory was in hand) and the caller decides what to do
        about it. The failure is committed rather than rolled back, because
        by then the cache has recorded upstream's refusal and that record is
        what keeps a broken origin from being hammered on the next run.

        A database's refusal of the write itself is recorded the same way,
        after rolling the half-written transaction back. It is not a fact
        about the site and it should be looked at -- it is logged with its
        traceback -- but the run it happens in is a sweep over every other
        site, and letting it escape would cost all of them their refresh
        over one source's lost race. The registration is left recording a
        failure, which is also what stops the next run from recognizing the
        digest and skipping the site that never wrote its links.

        An inventory that hashes to the one this source's last successful
        ingest read is recognized rather than re-read: the parse, the
        entity upsert, the link replacement, and the pruning that follows
        one are all skipped, the registration is stamped as it always is,
        and the result comes back flagged
        `~SourceIngestResult.unchanged` with every count zero. That is the
        ordinary outcome of a scheduled sweep over sites that have not
        republished, and it is also what keeps two overlapping runs from
        each rewriting a site's links behind the other. See
        `_is_already_ingested` for the three conditions, and
        `ingest_sources` for the pruning a run of nothing-but-skips still
        owes.

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

        A source *repointed* while its inventory is in flight ends the same
        way, and for the same kind of reason: every link parsed out of those
        bytes is resolved against the URL they were read from, so writing
        them would file one site's pages under a registration that now names
        another. The locked row's URL is compared with the fetched one
        before its digest is, so the one comparison covers the replace and
        the skip alike -- and no digest describing the old URL's bytes is
        stamped on a registration that has moved. ``update_source`` cleared
        the digest when it moved the URL and nothing puts it back, so the
        next run rebuilds the site's links from the new URL even when that
        URL serves byte-identical bytes.

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
            The source's outcome, or None if the registration was
            deregistered or repointed between its inventory being fetched
            and its links being written.
        """
        logger = self._logger.bind(source_id=source.id, url=source.url)
        try:
            fetched = await self._read_inventory(source, revalidate=revalidate)
        except _INGEST_FAILURES as exc:
            return await self._record_failure(source, exc, logger=logger)
        # Whatever the serve above wrote about the cache -- a cold miss's
        # stored inventory, or a bumped date_requested -- is committed here
        # and not held into the wait below. It is a fact about the cache
        # whatever becomes of this site's links, and everything else that
        # touches the row (the public GET, the refresh job) would otherwise
        # queue behind a registration lock and an entity-graph write it has
        # nothing to do with.
        await self._session.commit()

        locked = await self._source_store.lock_source(source.id)
        if locked is None:
            # Nothing of this ingest's own is pending -- the cache's
            # bookkeeping was committed above -- so this only closes the
            # transaction the lock read opened.
            await self._session.rollback()
            logger.info("Skipped intersphinx source deleted during its ingest")
            return None

        if locked.url != fetched.url:
            # Rolled back for the same reason the deregistration above is,
            # and checked before the digest so this one comparison covers
            # the skip and the replace alike.
            await self._session.rollback()
            logger.info(
                "Skipped intersphinx source repointed during its ingest",
                fetched_url=fetched.url,
                registered_url=locked.url,
            )
            return None

        if _is_already_ingested(locked, fetched):
            return await self._record_unchanged(locked, fetched, logger=logger)

        try:
            parsed = _parse_fetched_inventory(fetched)
        except _INGEST_FAILURES as exc:
            return await self._record_failure(locked, exc, logger=logger)

        try:
            replaced = await self._store_links(locked, parsed)
            pruned_count = await self._converge_entities()
            await self._source_store.record_ingest_outcome(
                locked.id,
                date_ingested=datetime.now(tz=UTC),
                status=SourceIngestStatus.success,
                error=None,
                content_digest=fetched.digest,
            )
            await self._session.commit()
        except _WRITE_FAILURES as exc:
            # Logged with its traceback here, because what the registry row
            # is given instead says nothing about which statement the
            # database refused.
            logger.exception(
                "The database refused an intersphinx source's links"
            )
            # The transaction is aborted, so nothing can be recorded on it:
            # the rollback throws away this site's half-written links and
            # gives the stamp below a transaction to be written in.
            await self._session.rollback()
            return await self._record_failure(
                locked, exc, logger=logger, detail=_WRITE_CONFLICT_MESSAGE
            )
        logger.info(
            "Ingested intersphinx source",
            entity_count=replaced.entity_count,
            link_count=replaced.link_count,
            pruned_count=pruned_count,
            cache_status=replaced.cache_status,
        )
        return SourceIngestResult(
            source_id=locked.id,
            # The registration's URL, which the guard above establishes is
            # also the one these links were resolved against: an ingest
            # whose site was repointed mid-flight never reaches here. The
            # title is free to have changed, and comes from the re-read row.
            url=locked.url,
            title=locked.title,
            status=SourceIngestStatus.success,
            unchanged=False,
            entity_count=replaced.entity_count,
            link_count=replaced.link_count,
            pruned_count=pruned_count,
            error=None,
            cache_status=replaced.cache_status,
        )

    async def _read_inventory(
        self, source: IntersphinxSource, *, revalidate: RevalidationMode
    ) -> _FetchedInventory:
        """Fetch one site's inventory, writing nothing of Ook's.

        Everything that can reach the origin lives here, and nothing that
        writes an entity or a link does, so `ingest_source` can put the
        registration lock between the two. The bytes are hashed but not yet
        parsed: the digest is what decides whether they need parsing at all,
        and it can only be compared against the registration once that is
        locked.

        Returns
        -------
        _FetchedInventory
            The inventory's bytes, their digest, and how fresh the copy
            they came from was.
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

        return _FetchedInventory(
            url=source.url,
            content=content,
            digest=hashlib.sha256(content).hexdigest(),
            cache_status=served.cache_status,
        )

    async def _store_links(
        self, source: IntersphinxSource, parsed: _ParsedInventory
    ) -> _ReplacedLinks:
        """Store one site's entities and replace its links.

        Called only with the source's registration row locked, which is what
        makes the replace's delete-then-insert safe against a concurrent
        ingest of the same site.

        The entity graph's own lock is taken here too, before the first
        write, and covers the convergence that follows this replace in the
        same transaction. It is what stands between this site's links and
        another site's global prune; see
        `~ook.storage.intersphinxentitystore.IntersphinxEntityStore.lock_entity_graph`.
        Taken after the registration lock, never before, so every writer
        that holds both takes them in the same order.

        Parameters
        ----------
        source
            The source as it stands under the lock, whose title every link
            written here carries.
        parsed
            The inventory `_read_inventory` fetched and
            `_parse_fetched_inventory` read.

        Returns
        -------
        _ReplacedLinks
            What the replace wrote, and how fresh the inventory it was
            built from was.
        """
        await self._entity_store.lock_entity_graph()
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

    async def _converge_entities(self) -> int:
        """Bring stored entities back in line with the links that exist.

        Two statements that only mean anything together, which is why they
        are never called apart: containment is recomputed from the links
        every source currently contributes, and then every entity left
        without a link is deleted. Run after any change to the links -- a
        per-source replace here, or a registration's deletion in
        `~ook.services.intersphinxsources.IntersphinxSourceService`.

        Both passes are global rather than scoped to the source whose links
        just changed, because both questions are: a module one site stopped
        documenting may still be documented by another, and the classes
        nested under it belong to whichever sites do.

        That global scope is exactly why the entity graph's lock is taken
        first. A caller that reached here through `_store_links` already
        holds it and takes it again for nothing; one that did not -- the
        sweep's own pass -- takes it here, which is the only place it could.
        Asking for it before the first write is what keeps a writer holding
        entity row locks from ever waiting on it, and so what keeps the two
        locks free of a cycle.

        Returns
        -------
        int
            The number of entities pruned.
        """
        await self._entity_store.lock_entity_graph()
        await self._entity_store.recompute_containment()
        return await self._entity_store.prune_orphan_entities()

    async def _converge_after_sweep(
        self, results: list[SourceIngestResult]
    ) -> int:
        """Converge stored entities on a run that replaced no links.

        Convergence normally rides on a source's link replacement, and a
        fleet whose sites have all stopped republishing performs none --
        which is the settled state of the system rather than an edge case.
        This is what such a run still owes, and it owes it twice over.

        Containment is *derived*, so its derivation can change with no site
        changing. A release that alters the rule
        `~ook.storage.intersphinxentitystore.IntersphinxEntityStore.recompute_containment`
        applies ships with no migration on the understanding that the next
        run rewrites the rows it describes; the digest skip is exactly what
        would make that untrue, since no source would ever replace and no
        replace would ever recompute. This pass is what makes a
        no-migration deploy converge on its own.

        And it is the backstop under the entity graph's lock. Every writer
        takes that lock and converges before it commits, so an entity left
        with no link is not a state the application reaches -- but a
        recompute and a prune over a settled fleet cost one pass of two
        statements that write nothing, which is a cheap way not to have to
        be certain of that.

        It runs in a transaction of its own, so it neither extends nor is
        rolled back with any source's ingest, and it takes the graph's lock
        like any other writer -- through `_converge_entities`. It is skipped
        when some source in the run did replace its links, because that
        source's convergence was global: it read every source's links rather
        than its own, so a pass late in a run has already answered for the
        whole of it. Keyed on a replace having happened rather than on its
        having pruned anything, since a replace that pruned nothing still
        recomputed containment over everything.

        Returns
        -------
        int
            The number of entities this pass deleted, which is zero when it
            was skipped -- and zero on a healthy settled fleet when it ran,
            where what it does is the recompute.
        """
        if any(
            result.status is SourceIngestStatus.success
            and not result.unchanged
            for result in results
        ):
            return 0
        pruned = await self._converge_entities()
        await self._session.commit()
        return pruned

    async def _record_unchanged(
        self,
        source: IntersphinxSource,
        fetched: _FetchedInventory,
        *,
        logger: BoundLogger,
    ) -> SourceIngestResult:
        """Stamp a recognized inventory as ingested without rewriting links.

        The registration is stamped exactly as a replacing ingest stamps
        it -- ``date_ingested`` answers "when did Ook last check this
        site?", which this run answered -- and the digest is rewritten to
        the value it already held, so the row says the same thing whichever
        path reached it.

        Parameters
        ----------
        source
            The source as it stands under its registration lock, whose URL
            `ingest_source` has already established is still the one
            *fetched* was read from.
        fetched
            The inventory whose digest matched what the source last
            ingested.
        logger
            The logger, already bound to this source.
        """
        await self._source_store.record_ingest_outcome(
            source.id,
            date_ingested=datetime.now(tz=UTC),
            status=SourceIngestStatus.success,
            error=None,
            content_digest=fetched.digest,
        )
        await self._session.commit()
        logger.info(
            "Skipped an intersphinx source whose inventory is unchanged",
            cache_status=fetched.cache_status,
        )
        return SourceIngestResult(
            source_id=source.id,
            url=source.url,
            title=source.title,
            status=SourceIngestStatus.success,
            unchanged=True,
            # Nothing was written, and these count writes rather than
            # contents: what the site documents is whatever the last
            # replacing ingest stored, and is not re-counted here.
            entity_count=0,
            link_count=0,
            pruned_count=0,
            error=None,
            cache_status=fetched.cache_status,
        )

    async def _record_failure(
        self,
        source: IntersphinxSource,
        error: Exception,
        *,
        logger: BoundLogger,
        detail: str | None = None,
    ) -> SourceIngestResult:
        """Stamp a source's failed ingest on its registry row and commit.

        The failure's own text describes the row unless *detail* replaces
        it, which is for the errors whose text describes Ook's SQL rather
        than the site. The log gets the full error either way.
        """
        described = detail or str(error)
        detail = described[:_MAX_ERROR_LENGTH] or type(error).__name__
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
            unchanged=False,
            entity_count=0,
            link_count=0,
            pruned_count=0,
            error=detail,
            # A failed ingest was served no inventory, so it has no
            # freshness to describe -- not even a stale one.
            cache_status=None,
        )
