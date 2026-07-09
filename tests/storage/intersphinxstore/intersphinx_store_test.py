"""Tests for the IntersphinxInventoryStore."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from ook.dbschema.intersphinx import SqlIntersphinxInventory
from ook.domain.intersphinx import IntersphinxInventory, InventoryFetchStatus
from ook.factory import Factory


def _make_inventory(
    url: str,
    *,
    content: bytes | None = b"objects.inv payload",
    content_type: str | None = "application/octet-stream",
    etag: str | None = '"abc123"',
    last_modified: str | None = "Wed, 09 Jul 2026 00:00:00 GMT",
    date_fetched: datetime | None,
    date_requested: datetime,
    last_fetch_status: InventoryFetchStatus | None = (
        InventoryFetchStatus.success
    ),
    last_fetch_error: str | None = None,
) -> IntersphinxInventory:
    return IntersphinxInventory(
        url=url,
        content=content,
        content_type=content_type,
        etag=etag,
        last_modified=last_modified,
        date_fetched=date_fetched,
        date_requested=date_requested,
        last_fetch_status=last_fetch_status,
        last_fetch_error=last_fetch_error,
    )


@pytest.mark.asyncio
async def test_upsert_get_roundtrip(factory: Factory) -> None:
    """Upsert then get-by-URL round-trips every stored field."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        url = "https://docs.example.com/en/latest/objects.inv"

        # An uncached URL resolves to None.
        assert await store.get_inventory(url) is None

        inventory = _make_inventory(url, date_fetched=now, date_requested=now)
        await store.upsert_inventory(inventory)

        stored = await store.get_inventory(url)
        assert stored == inventory


@pytest.mark.asyncio
async def test_upsert_updates_in_place(factory: Factory) -> None:
    """Upserting an existing URL updates the row without duplicating it."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        url = "https://docs.example.com/en/latest/objects.inv"

        await store.upsert_inventory(
            _make_inventory(
                url,
                content=b"old",
                etag='"old-etag"',
                date_fetched=now,
                date_requested=now,
            )
        )
        later = now + timedelta(hours=2)
        updated = _make_inventory(
            url,
            content=b"new",
            etag='"new-etag"',
            date_fetched=later,
            date_requested=later,
        )
        await store.upsert_inventory(updated)

        # Exactly one row exists for the URL, carrying the new values.
        count = (
            await factory.db_session.execute(
                select(func.count())
                .select_from(SqlIntersphinxInventory)
                .where(SqlIntersphinxInventory.url == url)
            )
        ).scalar_one()
        assert count == 1
        assert await store.get_inventory(url) == updated


@pytest.mark.asyncio
async def test_negative_cache_row(factory: Factory) -> None:
    """A failure row with no content stores and retrieves (negative
    cache).
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        url = "https://down.example.com/objects.inv"

        negative = _make_inventory(
            url,
            content=None,
            content_type=None,
            etag=None,
            last_modified=None,
            date_fetched=now,
            date_requested=now,
            last_fetch_status=InventoryFetchStatus.failure,
            last_fetch_error="502 Bad Gateway",
        )
        await store.upsert_inventory(negative)

        stored = await store.get_inventory(url)
        assert stored is not None
        assert stored.content is None
        assert stored.last_fetch_status is InventoryFetchStatus.failure
        assert stored.last_fetch_error == "502 Bad Gateway"
        assert stored == negative


@pytest.mark.asyncio
async def test_touch_date_requested(factory: Factory) -> None:
    """Touching an inventory bumps its date_requested; unknown URLs are a
    no-op.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        url = "https://docs.example.com/en/latest/objects.inv"

        # Touching an uncached URL reports no row updated.
        assert await store.touch_date_requested(url, now=now) is False

        await store.upsert_inventory(
            _make_inventory(url, date_fetched=now, date_requested=now)
        )

        requested_at = now + timedelta(days=1)
        assert await store.touch_date_requested(url, now=requested_at) is True
        stored = await store.get_inventory(url)
        assert stored is not None
        assert stored.date_requested == requested_at
        # Touching does not alter the freshness anchor.
        assert stored.date_fetched == now


@pytest.mark.asyncio
async def test_get_stale_active_inventories(factory: Factory) -> None:
    """Stale-and-recently-requested selection returns only inventories
    past the TTL that were requested within the active window.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        ttl = timedelta(hours=1)
        active_window = timedelta(days=30)

        # Stale fetch, recently requested: due.
        await store.upsert_inventory(
            _make_inventory(
                "https://due.example.com/stale-active/objects.inv",
                date_fetched=now - timedelta(hours=2),
                date_requested=now - timedelta(days=1),
            )
        )
        # Fresh fetch, recently requested: not due (within TTL).
        await store.upsert_inventory(
            _make_inventory(
                "https://fresh.example.com/objects.inv",
                date_fetched=now - timedelta(minutes=10),
                date_requested=now - timedelta(days=1),
            )
        )
        # Stale fetch, but requested long ago: not due (outside window).
        await store.upsert_inventory(
            _make_inventory(
                "https://inactive.example.com/objects.inv",
                date_fetched=now - timedelta(hours=5),
                date_requested=now - timedelta(days=60),
            )
        )
        # Never fetched, recently requested: due (null fetch is stale).
        await store.upsert_inventory(
            _make_inventory(
                "https://due.example.com/never-fetched/objects.inv",
                date_fetched=None,
                last_fetch_status=None,
                date_requested=now - timedelta(hours=1),
            )
        )

        due = await store.get_stale_active_inventories(
            now=now, ttl=ttl, active_window=active_window
        )
        assert {inv.url for inv in due} == {
            "https://due.example.com/stale-active/objects.inv",
            "https://due.example.com/never-fetched/objects.inv",
        }

        # A limit caps the number of returned inventories.
        limited = await store.get_stale_active_inventories(
            now=now, ttl=ttl, active_window=active_window, limit=1
        )
        assert len(limited) == 1
