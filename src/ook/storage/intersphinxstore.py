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
        rows. Every non-key column is overwritten unconditionally, which is
        how a successful re-fetch replaces the prior content in place. Use
        `upsert_fetch_failure` for the negative-cache path, which must not
        overwrite a content-bearing row.

        Parameters
        ----------
        inventory
            The inventory record to store. A record with null content and a
            ``failure`` status is the negative-cache shape.
        """
        values = self._row_values(inventory)
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

    async def upsert_fetch_failure(
        self, inventory: IntersphinxInventory
    ) -> None:
        """Store a cold-miss fetch failure without clobbering good content.

        This is the negative-cache write. Unlike `upsert_inventory`, the
        ``ON CONFLICT DO UPDATE`` is gated on the existing row having no
        content, so the failure row only inserts when the URL is uncached
        and only updates an existing row that is itself contentless. When a
        content-bearing row already exists — for example, a concurrent
        request stored a good copy between this request's cold miss and its
        failure — the write is skipped and the good copy stands. This is what
        makes the negative-cache invariant hold under concurrency rather than
        only single-threaded. That same guard is what keeps a failure from
        clearing a content-bearing row's resolved-redirect columns.

        Parameters
        ----------
        inventory
            The negative-cache record to store: null content, null
            resolved-redirect columns, and a ``failure`` status.
        """
        values = self._row_values(inventory)
        insert_stmt = pg_insert(SqlIntersphinxInventory).values(**values)
        update_columns = {
            key: value for key, value in values.items() if key != "url"
        }
        # Only insert (no conflict) or update a contentless row: the WHERE
        # guards the DO UPDATE against the existing row's content, so a
        # failure never displaces a content-bearing copy.
        await self._session.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=["url"],
                set_=update_columns,
                where=SqlIntersphinxInventory.content.is_(None),
            )
        )
        await self._session.flush()

    async def update_refresh_outcome(
        self, inventory: IntersphinxInventory
    ) -> None:
        """Persist a proactive-refresh outcome without touching
        ``date_requested``.

        This is the refresh path's write. Unlike `upsert_inventory`, which
        rewrites every non-key column, this updates only the fetch-outcome
        columns — content, content type, validators, fetch time, fetch
        status, and the resolved-redirect columns — and deliberately leaves
        ``date_requested`` alone. The refresh
        job reads a row at due-list selection time and writes it back after an
        HTTP round-trip; a client request may bump ``date_requested`` in that
        window, so rewriting the stale value would silently shorten the
        inventory's active window. This method never inserts: the refresh path
        only ever writes rows that already exist.

        Parameters
        ----------
        inventory
            The refreshed inventory whose outcome columns to persist. Its
            ``date_requested`` value is ignored.
        """
        values = self._row_values(inventory)
        # The URL is the row key and date_requested is owned by the request
        # path, so neither is written here.
        del values["url"]
        del values["date_requested"]
        await self._session.execute(
            update(SqlIntersphinxInventory)
            .where(SqlIntersphinxInventory.url == inventory.url)
            .values(**values)
        )
        await self._session.flush()

    async def update_refresh_failure(
        self, url: str, *, now: datetime, error: str
    ) -> None:
        """Record a failed proactive refresh without touching the stored copy.

        This is the refresh path's failure write, the counterpart to
        `update_refresh_outcome`. It writes only the failure columns and the
        backoff marker — ``last_fetch_status``, ``last_fetch_error``, and
        ``date_refresh_failed`` — and touches no other column *by
        construction* rather than by guard: content, its validators, its
        resolved-redirect columns, and the ``date_fetched`` freshness anchor
        are all left as the last successful fetch wrote them. That is what
        keeps the stored copy serving stale at its true reported age, and it
        is also why a failure cannot clobber content a concurrent cold-miss
        request stored between the due-list read and this write.

        The marker is what backs the row off: `get_stale_active_inventories`
        holds a row out of the due list for a TTL after its last failure, so
        a broken inventory is retried on the normal refresh cadence rather
        than on every run. Like `update_refresh_outcome`, this never inserts;
        the refresh path only ever writes rows that already exist.

        Parameters
        ----------
        url
            The URL of the inventory whose refresh failed.
        now
            The time of the failed attempt.
        error
            A description of the failure, stored as ``last_fetch_error``.
        """
        await self._session.execute(
            update(SqlIntersphinxInventory)
            .where(SqlIntersphinxInventory.url == url)
            .values(
                last_fetch_status=InventoryFetchStatus.failure.value,
                last_fetch_error=error,
                date_refresh_failed=now,
            )
        )
        await self._session.flush()

    @staticmethod
    def _row_values(inventory: IntersphinxInventory) -> dict[str, object]:
        """Build the column values for an insert or upsert of an inventory."""
        return {
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
            "resolved_url": inventory.resolved_url,
            "resolved_redirect_permanent": (
                inventory.resolved_redirect_permanent
            ),
            "date_refresh_failed": inventory.date_refresh_failed,
        }

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
        TTL (or it has never been fetched), it was requested by a client
        within the active window, and its last refresh failure — if any — is
        itself older than the TTL. Inventories requested longer ago than the
        active window are skipped so the refresh job doesn't revalidate
        inventories no client is using, and a recently-failed inventory backs
        off so a broken origin is retried on the normal refresh cadence
        instead of on every run. Both cutoffs use the same TTL: a failed
        attempt costs an inventory exactly the interval a successful one
        would have bought it.

        Parameters
        ----------
        now
            The current time.
        ttl
            The freshness TTL; inventories fetched earlier than
            ``now - ttl`` are stale, and a refresh failure earlier than
            ``now - ttl`` no longer holds its inventory back.
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
                (SqlIntersphinxInventory.date_refresh_failed.is_(None))
                | (SqlIntersphinxInventory.date_refresh_failed < stale_cutoff),
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
            resolved_url=row.resolved_url,
            resolved_redirect_permanent=row.resolved_redirect_permanent,
            date_refresh_failed=row.date_refresh_failed,
        )
