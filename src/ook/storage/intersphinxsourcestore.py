"""Storage interface for the intersphinx documentation source registry."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.stdlib import BoundLogger

from ook.dbschema.intersphinxsources import SqlIntersphinxSource
from ook.domain.intersphinxsources import IntersphinxSource, SourceIngestStatus

__all__ = ["IntersphinxSourceStore"]


class IntersphinxSourceStore:
    """An interface to the registry of intersphinx documentation sources.

    Follows the store conventions of an ``AsyncSession`` plus a
    ``BoundLogger`` constructor with caller-managed transactions.
    """

    def __init__(self, session: AsyncSession, logger: BoundLogger) -> None:
        self._session = session
        self._logger = logger

    async def add_source(
        self, *, url: str, title: str, enabled: bool = True
    ) -> IntersphinxSource:
        """Register a documentation source.

        The observability columns are left unset: a source that has never
        been ingested is distinguishable from one whose last ingest
        succeeded, which is what makes a newly registered source visible as
        pending rather than healthy.

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
            The registered source, with its newly assigned ID.

        Raises
        ------
        sqlalchemy.exc.IntegrityError
            Raised if the inventory URL is already registered. Left to the
            caller so the API layer can report the conflict in its own
            terms.
        """
        row = SqlIntersphinxSource(
            url=url,
            title=title,
            enabled=enabled,
            date_ingested=None,
            last_status=None,
            last_error=None,
        )
        self._session.add(row)
        await self._session.flush()
        return self._to_domain(row)

    async def get_source(self, source_id: int) -> IntersphinxSource | None:
        """Get a registered source by its ID.

        Parameters
        ----------
        source_id
            The source's database ID.

        Returns
        -------
        IntersphinxSource or None
            The source, or None if no source has that ID.
        """
        row = await self._session.get(SqlIntersphinxSource, source_id)
        return None if row is None else self._to_domain(row)

    async def get_source_by_url(self, url: str) -> IntersphinxSource | None:
        """Get a registered source by its inventory URL.

        Parameters
        ----------
        url
            The full URL of the site's ``objects.inv`` inventory.

        Returns
        -------
        IntersphinxSource or None
            The source, or None if the URL is not registered.
        """
        row = (
            await self._session.execute(
                select(SqlIntersphinxSource).where(
                    SqlIntersphinxSource.url == url
                )
            )
        ).scalar_one_or_none()
        return None if row is None else self._to_domain(row)

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
            The registered sources, ordered by URL. Ordered by URL rather
            than by ID so the listing is stable across a delete and
            re-register of the same site.
        """
        stmt = select(SqlIntersphinxSource).order_by(SqlIntersphinxSource.url)
        if enabled_only:
            stmt = stmt.where(SqlIntersphinxSource.enabled.is_(True))
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_domain(row) for row in rows]

    async def update_source(
        self,
        source_id: int,
        *,
        url: str | None = None,
        title: str | None = None,
        enabled: bool | None = None,
    ) -> IntersphinxSource | None:
        """Update a registered source's editable fields.

        Only the fields given are written, so an update that renames a
        source does not have to restate whether it is enabled. The
        observability columns are not editable here -- they are written by
        an ingest run through `record_ingest_outcome`.

        Parameters
        ----------
        source_id
            The source's database ID.
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
        """
        row = await self._session.get(SqlIntersphinxSource, source_id)
        if row is None:
            return None
        if url is not None:
            row.url = url
        if title is not None:
            row.title = title
        if enabled is not None:
            row.enabled = enabled
        await self._session.flush()
        return self._to_domain(row)

    async def delete_source(self, source_id: int) -> bool:
        """Delete a registered source and, by cascade, its links.

        The entities those links pointed at are left behind: another source
        may document the same object, and an entity nothing documents any
        more is the pruning path's business rather than this one's.

        Parameters
        ----------
        source_id
            The source's database ID.

        Returns
        -------
        bool
            True if a source was deleted, False if none had that ID.
        """
        result = await self._session.execute(
            delete(SqlIntersphinxSource).where(
                SqlIntersphinxSource.id == source_id
            )
        )
        await self._session.flush()
        return cast("CursorResult", result).rowcount > 0

    async def record_ingest_outcome(
        self,
        source_id: int,
        *,
        date_ingested: datetime,
        status: SourceIngestStatus,
        error: str | None = None,
    ) -> bool:
        """Stamp the outcome of an ingest run onto a source's row.

        Written whether the run succeeded or failed, because the useful
        question about a source is when Ook last *tried* it: a row whose
        ``date_ingested`` is old is stale regardless of what its last
        attempt returned.

        Parameters
        ----------
        source_id
            The source's database ID.
        date_ingested
            The time of the ingest attempt.
        status
            The attempt's outcome.
        error
            A description of the failure, or None on success. Passing None
            on success is what clears a previous run's error.

        Returns
        -------
        bool
            True if the outcome was recorded, False if no source had that
            ID.
        """
        result = await self._session.execute(
            update(SqlIntersphinxSource)
            .where(SqlIntersphinxSource.id == source_id)
            .values(
                date_ingested=date_ingested,
                last_status=status.value,
                last_error=error,
            )
        )
        await self._session.flush()
        return cast("CursorResult", result).rowcount > 0

    def _to_domain(self, row: SqlIntersphinxSource) -> IntersphinxSource:
        """Convert a registry row into its domain model."""
        return IntersphinxSource(
            id=row.id,
            url=row.url,
            title=row.title,
            enabled=row.enabled,
            date_ingested=row.date_ingested,
            last_status=(
                None
                if row.last_status is None
                else SourceIngestStatus(row.last_status)
            ),
            last_error=row.last_error,
        )
