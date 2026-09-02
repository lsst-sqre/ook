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
from ook.services.intersphinx import IntersphinxCacheService
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


class IntersphinxIngestService:
    """Ingests registered documentation sites' Sphinx object inventories.

    Unlike the stores it drives, this service owns its transaction
    boundaries and must be called without a surrounding transaction: each
    source's links are replaced and committed on their own, so a site whose
    inventory cannot be read leaves every other site's ingest committed, and
    a crash mid-run keeps the sources already done.

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

        Returns
        -------
        IntersphinxIngestSummary
            Each enabled source's outcome, in the order they were visited.
        """
        sources = await self._source_store.list_sources(enabled_only=True)
        # Commit the registry read as its own short transaction: no snapshot
        # should be held open across the per-source inventory fetches below.
        await self._session.commit()

        results = [await self.ingest_source(source) for source in sources]
        summary = IntersphinxIngestSummary(results=results)
        self._logger.info(
            "Completed intersphinx ingest",
            considered=len(results),
            succeeded=summary.succeeded,
            failed=summary.failed,
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
            its links are replaced against.
        """
        source = await self._source_store.get_source_by_url(url)
        await self._session.commit()
        if source is None:
            raise NotFoundError(
                message=(
                    f"No documentation source is registered with the "
                    f"inventory URL {url!r}."
                )
            )
        return await self.ingest_source(source)

    async def ingest_source(
        self, source: IntersphinxSource
    ) -> SourceIngestResult:
        """Ingest one documentation source and commit the outcome.

        The whole of one site's ingest -- its entities, the replacement of
        its links, and the pruning that replacement exposes -- is one
        transaction, so a reader never sees the site's links half replaced.
        The registry stamp is committed with it, which is what makes
        ``last_status`` a description of the links actually stored.

        A fetch or parse failure is recorded and returned rather than
        raised: the site's previous links are kept (nothing was deleted
        before the inventory was in hand) and the caller decides what to do
        about it. The failure is committed rather than rolled back, because
        by then the cache has recorded upstream's refusal and that record is
        what keeps a broken origin from being hammered on the next run.

        Parameters
        ----------
        source
            The registered source to ingest.

        Returns
        -------
        SourceIngestResult
            The source's outcome.
        """
        logger = self._logger.bind(source_id=source.id, url=source.url)
        try:
            entities, link_count = await self._replace_links(source)
            pruned_count = await self._entity_store.prune_orphan_entities()
        except _INGEST_FAILURES as exc:
            return await self._record_failure(source, exc, logger=logger)

        await self._source_store.record_ingest_outcome(
            source.id,
            date_ingested=datetime.now(tz=UTC),
            status=SourceIngestStatus.success,
            error=None,
        )
        await self._session.commit()
        logger.info(
            "Ingested intersphinx source",
            entity_count=entities,
            link_count=link_count,
            pruned_count=pruned_count,
        )
        return SourceIngestResult(
            source_id=source.id,
            url=source.url,
            title=source.title,
            status=SourceIngestStatus.success,
            entity_count=entities,
            link_count=link_count,
            pruned_count=pruned_count,
            error=None,
        )

    async def _replace_links(
        self, source: IntersphinxSource
    ) -> tuple[int, int]:
        """Store one site's entities and replace its links.

        Returns
        -------
        tuple
            The number of entities stored and the number of links written.
        """
        served = await self._cache_service.get_inventory(source.url)
        content = served.inventory.content
        if content is None:
            # The cache serves content or raises, so this is unreachable in
            # practice; reported as a parse failure rather than asserted
            # because an empty cache row is a fact about one site and must
            # not stop the run.
            raise InventoryParseError(
                f"The cached inventory for {source.url} holds no content."
            )

        entities = build_entities(parse_inventory(content))
        entity_ids = await self._entity_store.upsert_entities(entities)
        links = self._build_links(source, entities, entity_ids)
        link_count = await self._entity_store.replace_source_links(
            source.id, links, collection_title=source.title
        )
        return len(entity_ids), link_count

    def _build_links(
        self,
        source: IntersphinxSource,
        entities: list[InventoryEntity],
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
        for entity in entities:
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
                    html_url=urljoin(source.url, entity.uri),
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
        )
