"""Service for the registry of intersphinx documentation sources."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from structlog.stdlib import BoundLogger

from ook.domain.intersphinxsources import IntersphinxSource
from ook.exceptions import ConflictError
from ook.storage.intersphinxentitystore import IntersphinxEntityStore
from ook.storage.intersphinxsourcestore import IntersphinxSourceStore

__all__ = ["IntersphinxSourceService"]

URL_UNIQUE_INDEX = "ix_intersphinx_source_url"
"""The unique index that makes an inventory URL a source's identity.

Named here so a write that trips *some other* future constraint is not
reported to the client as a duplicate URL.
"""


class IntersphinxSourceService:
    """Service for managing the registry of documentation sources Ook
    ingests intersphinx inventories from.

    The store deliberately leaves a duplicate inventory URL as the
    database's own `~sqlalchemy.exc.IntegrityError` so the layer with an
    API to speak for can report it; this service is that layer.

    Parameters
    ----------
    source_store
        The source registry store.
    entity_store
        The store of entities and their links, which a deletion has to
        converge: the links a deregistered source contributed go with it,
        and the entities they were the last reason to keep go with them.
    logger
        The logger.
    """

    def __init__(
        self,
        *,
        source_store: IntersphinxSourceStore,
        entity_store: IntersphinxEntityStore,
        logger: BoundLogger,
    ) -> None:
        self._source_store = source_store
        self._entity_store = entity_store
        self._logger = logger

    async def register_source(
        self, *, url: str, title: str, enabled: bool = True
    ) -> IntersphinxSource:
        """Register a documentation source.

        Parameters
        ----------
        url
            The full URL of the site's ``objects.inv`` inventory.
        title
            The human title of the documentation site.
        enabled
            Whether ingest runs should visit the source.

        Returns
        -------
        IntersphinxSource
            The registered source, with its newly assigned ID and its
            observability fields unset.

        Raises
        ------
        ConflictError
            Raised if the inventory URL is already registered.
        """
        try:
            source = await self._source_store.add_source(
                url=url, title=title, enabled=enabled
            )
        except IntegrityError as e:
            self._raise_for_duplicate_url(e, url=url)
            raise
        self._logger.info(
            "Registered intersphinx source",
            source_id=source.id,
            url=source.url,
            enabled=source.enabled,
        )
        return source

    async def get_source(self, source_id: int) -> IntersphinxSource | None:
        """Get a registered source by its ID.

        Parameters
        ----------
        source_id
            The registration's ID.

        Returns
        -------
        IntersphinxSource or None
            The source, or None if no source has that ID.
        """
        return await self._source_store.get_source(source_id)

    async def list_sources(
        self, *, enabled_only: bool = False
    ) -> list[IntersphinxSource]:
        """List registered sources, ordered by inventory URL.

        Parameters
        ----------
        enabled_only
            If true, list only the sources ingest runs visit.

        Returns
        -------
        list of IntersphinxSource
            The registered sources, ordered by URL.
        """
        return await self._source_store.list_sources(enabled_only=enabled_only)

    async def update_source(
        self,
        source_id: int,
        *,
        url: str | None = None,
        title: str | None = None,
        enabled: bool | None = None,
    ) -> IntersphinxSource | None:
        """Update a registered source's editable fields.

        Only the fields given are written. The observability fields are not
        editable here: they are written by ingest runs.

        Parameters
        ----------
        source_id
            The registration's ID.
        url
            The new inventory URL, or None to leave it.
        title
            The new human title, or None to leave it.
        enabled
            The new enabled flag, or None to leave it.

        Returns
        -------
        IntersphinxSource or None
            The updated source, or None if no source has that ID.

        Raises
        ------
        ConflictError
            Raised if the new inventory URL is already registered to a
            different source.
        """
        try:
            source = await self._source_store.update_source(
                source_id, url=url, title=title, enabled=enabled
            )
        except IntegrityError as e:
            self._raise_for_duplicate_url(e, url=url)
            raise
        if source is not None:
            self._logger.info(
                "Updated intersphinx source",
                source_id=source.id,
                url=source.url,
                enabled=source.enabled,
            )
        return source

    async def delete_source(self, source_id: int) -> bool:
        """Delete a registered source and, by cascade, its links.

        The entities are then brought back in line with the links that
        remain, in the same transaction, so a deregistered site leaves
        nothing of itself behind: an object only it documented is gone, and
        an object it merely contained -- a class another site documents,
        nested under a module whose page only this one published -- becomes
        top level. Deleting the last source empties the domain.

        Converging here rather than leaving it to the next ingest run is
        what makes the deletion mean what the API says it does. The run is
        scheduled, and until it happened the Links API would keep serving a
        hierarchy propped up by a site nobody is ingesting any more.

        Two locks are taken before anything is written, in the order every
        ingest takes them: the registration row, then the entity graph. The
        registration lock is what answers whether there is a source to
        delete at all -- a row it cannot find is one no longer there --
        and the graph lock is what keeps this convergence from pruning an
        entity a concurrent ingest has linked but not yet committed. Taking
        them the other way round would deadlock against an ingest of the
        same site, which holds the registration while it waits for the
        graph.

        Parameters
        ----------
        source_id
            The registration's ID.

        Returns
        -------
        bool
            True if a source was deleted, False if none had that ID.
        """
        if await self._source_store.lock_source(source_id) is None:
            return False

        await self._entity_store.lock_entity_graph()
        await self._source_store.delete_source(source_id)
        await self._entity_store.recompute_containment()
        pruned = await self._entity_store.prune_orphan_entities()
        self._logger.info(
            "Deleted intersphinx source",
            source_id=source_id,
            pruned_count=pruned,
        )
        return True

    def _raise_for_duplicate_url(
        self, error: IntegrityError, *, url: str | None
    ) -> None:
        """Re-report a duplicate-URL integrity error as a conflict.

        Returns without raising when the error came from anything but the
        inventory URL's unique index, so the caller can re-raise it as the
        server-side failure it is rather than blaming the client's URL.
        """
        if URL_UNIQUE_INDEX not in str(error):
            return
        raise ConflictError(
            message=(
                f"The inventory URL {url!r} is already registered. Update"
                " or delete the existing registration instead."
            ),
        ) from error
