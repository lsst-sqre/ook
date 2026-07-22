"""Tests for the IntersphinxInventoryStore."""

from __future__ import annotations

from dataclasses import replace
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
async def test_failure_upsert_preserves_existing_content(
    factory: Factory,
) -> None:
    """A fetch-failure upsert never displaces a content-bearing row.

    This is the store-level guard for the negative-cache invariant: a
    concurrent cold-miss failure must not clobber a good copy that another
    request stored first.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        url = "https://docs.example.com/en/latest/objects.inv"

        good = _make_inventory(
            url,
            content=b"good payload",
            etag='"good-etag"',
            date_fetched=now,
            date_requested=now,
        )
        await store.upsert_inventory(good)

        # A racing failure upsert must be skipped: the good row stands.
        await store.upsert_fetch_failure(
            _make_inventory(
                url,
                content=None,
                content_type=None,
                etag=None,
                last_modified=None,
                date_fetched=now + timedelta(hours=1),
                date_requested=now + timedelta(hours=1),
                last_fetch_status=InventoryFetchStatus.failure,
                last_fetch_error="502 Bad Gateway",
            )
        )

        stored = await store.get_inventory(url)
        assert stored == good


@pytest.mark.asyncio
async def test_failure_upsert_updates_contentless_row(
    factory: Factory,
) -> None:
    """A fetch-failure upsert refreshes an existing contentless row."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        url = "https://down.example.com/objects.inv"

        first = _make_inventory(
            url,
            content=None,
            content_type=None,
            etag=None,
            last_modified=None,
            date_fetched=now - timedelta(hours=1),
            date_requested=now - timedelta(hours=1),
            last_fetch_status=InventoryFetchStatus.failure,
            last_fetch_error="500 Internal Server Error",
        )
        await store.upsert_fetch_failure(first)

        second = _make_inventory(
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
        await store.upsert_fetch_failure(second)

        # The contentless row is updated in place with the fresher failure.
        assert await store.get_inventory(url) == second


@pytest.mark.asyncio
async def test_failure_upsert_inserts_when_absent(factory: Factory) -> None:
    """A fetch-failure upsert inserts a negative-cache row when none
    exists.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        url = "https://new-down.example.com/objects.inv"

        assert await store.get_inventory(url) is None

        failure = _make_inventory(
            url,
            content=None,
            content_type=None,
            etag=None,
            last_modified=None,
            date_fetched=now,
            date_requested=now,
            last_fetch_status=InventoryFetchStatus.failure,
            last_fetch_error="Upstream request for the inventory timed out",
        )
        await store.upsert_fetch_failure(failure)

        assert await store.get_inventory(url) == failure


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
async def test_update_refresh_outcome_preserves_date_requested(
    factory: Factory,
) -> None:
    """A refresh-outcome write updates content and validators in place but
    leaves ``date_requested`` untouched.

    The refresh path reads a row at due-list selection time and writes its
    outcome back after an HTTP round-trip. A client request may bump
    ``date_requested`` in that window, so the write must not revert it to the
    stale value or it would silently shorten the inventory's active window.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        url = "https://docs.example.com/en/latest/objects.inv"

        # Seed a stale row; this is the read the refresh path starts from.
        stale_read = _make_inventory(
            url,
            content=b"old payload",
            etag='"old-etag"',
            last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
            date_fetched=now - timedelta(hours=2),
            date_requested=now - timedelta(days=1),
        )
        await store.upsert_inventory(stale_read)

        # A concurrent client request touches date_requested after the read.
        touched_at = now
        assert await store.touch_date_requested(url, now=touched_at) is True

        # The refresh path writes its outcome, built from the stale read.
        refreshed = replace(
            stale_read,
            content=b"new payload",
            etag='"new-etag"',
            last_modified="Fri, 10 Jul 2026 00:00:00 GMT",
            date_fetched=now,
        )
        await store.update_refresh_outcome(refreshed)

        stored = await store.get_inventory(url)
        assert stored is not None
        # The concurrent client's newer date_requested is preserved, not
        # reverted to the value the refresh path read.
        assert stored.date_requested == touched_at
        # The refresh-outcome columns are updated.
        assert stored.content == b"new payload"
        assert stored.etag == '"new-etag"'
        assert stored.last_modified == "Fri, 10 Jul 2026 00:00:00 GMT"
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
