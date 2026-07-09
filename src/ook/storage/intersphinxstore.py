"""Storage interface for cached intersphinx inventories."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.stdlib import BoundLogger

from ook.dbschema.intersphinx import SqlIntersphinxInventory
from ook.domain.intersphinx import IntersphinxInventory, InventoryFetchStatus

__all__ = ["IntersphinxInventoryStore"]


class IntersphinxInventoryStore:
    """Interface for storing cached intersphinx inventories in a database.

    Follows the store conventions of an ``AsyncSession`` plus a
    ``BoundLogger`` constructor with caller-managed transactions.
    """

    def __init__(self, session: AsyncSession, logger: BoundLogger) -> None:
        self._session = session
        self._logger = logger

    async def upsert_inventory(self, inventory: IntersphinxInventory) -> None:
        """Insert or update a cached inventory keyed by its URL.

        An existing row for the same URL is updated in place via a Postgres
        ``INSERT ... ON CONFLICT`` upsert, so a URL never yields duplicate
        rows.

        Parameters
        ----------
        inventory
            The inventory record to store. A record with null content and a
            ``failure`` status is the negative-cache shape.
        """
        values = {
            "url": inventory.url,
            "content": inventory.content,
            "content_type": inventory.content_type,
            "etag": inventory.etag,
            "last_modified": inventory.last_modified,
            "date_fetched": inventory.date_fetched,
            "date_requested": inventory.date_requested,
            "last_fetch_status": (
                inventory.last_fetch_status.value
                if inventory.last_fetch_status is not None
                else None
            ),
            "last_fetch_error": inventory.last_fetch_error,
        }
        insert_stmt = pg_insert(SqlIntersphinxInventory).values(**values)
        # The URL is the conflict target; every non-key column is refreshed
        # so a re-fetch overwrites the prior state in place.
        update_columns = {
            key: value for key, value in values.items() if key != "url"
        }
        await self._session.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=["url"], set_=update_columns
            )
        )
        await self._session.flush()

    async def get_inventory(self, url: str) -> IntersphinxInventory | None:
        """Get a cached inventory by its URL.

        Parameters
        ----------
        url
            The full origin ``objects.inv`` URL to look up.

        Returns
        -------
        IntersphinxInventory or None
            The cached inventory, or None if the URL is not cached.
        """
        row = (
            await self._session.execute(
                select(SqlIntersphinxInventory).where(
                    SqlIntersphinxInventory.url == url
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return self._to_domain(row)

    async def touch_date_requested(
        self, url: str, *, now: datetime | None = None
    ) -> bool:
        """Update a cached inventory's last-requested time.

        Parameters
        ----------
        url
            The full origin ``objects.inv`` URL that was requested.
        now
            The request time to record. Defaults to the current time.

        Returns
        -------
        bool
            True if a row was updated, False if the URL is not cached.
        """
        if now is None:
            now = datetime.now(tz=UTC)
        result = await self._session.execute(
            update(SqlIntersphinxInventory)
            .where(SqlIntersphinxInventory.url == url)
            .values(date_requested=now)
        )
        await self._session.flush()
        return cast("CursorResult", result).rowcount > 0

    async def get_stale_active_inventories(
        self,
        *,
        now: datetime,
        ttl: timedelta,
        active_window: timedelta,
        limit: int | None = None,
    ) -> list[IntersphinxInventory]:
        """Enumerate cached inventories that are due for a refresh.

        An inventory is due when its last fetch is older than the freshness
        TTL (or it has never been fetched) and it was requested by a client
        within the active window. Inventories requested longer ago than the
        active window are skipped so the refresh job doesn't revalidate
        inventories no client is using.

        Parameters
        ----------
        now
            The current time.
        ttl
            The freshness TTL; inventories fetched earlier than
            ``now - ttl`` are stale.
        active_window
            The active window; only inventories requested at or after
            ``now - active_window`` are eligible.
        limit
            The maximum number of inventories to return, or None for no
            limit.

        Returns
        -------
        list of IntersphinxInventory
            The due inventories, stalest fetch first.
        """
        stale_cutoff = now - ttl
        active_cutoff = now - active_window
        stmt = (
            select(SqlIntersphinxInventory)
            .where(
                SqlIntersphinxInventory.date_requested >= active_cutoff,
                (SqlIntersphinxInventory.date_fetched.is_(None))
                | (SqlIntersphinxInventory.date_fetched < stale_cutoff),
            )
            .order_by(SqlIntersphinxInventory.date_fetched.asc().nullsfirst())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_domain(row) for row in rows]

    @staticmethod
    def _to_domain(
        row: SqlIntersphinxInventory,
    ) -> IntersphinxInventory:
        """Convert a SQLAlchemy row to a domain model."""
        return IntersphinxInventory(
            url=row.url,
            content=row.content,
            content_type=row.content_type,
            etag=row.etag,
            last_modified=row.last_modified,
            date_fetched=row.date_fetched,
            date_requested=row.date_requested,
            last_fetch_status=(
                InventoryFetchStatus(row.last_fetch_status)
                if row.last_fetch_status is not None
                else None
            ),
            last_fetch_error=row.last_fetch_error,
        )
