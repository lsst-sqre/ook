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
    resolved_url: str | None = None,
    resolved_redirect_permanent: bool | None = None,
    date_refresh_failed: datetime | None = None,
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
        resolved_url=resolved_url,
        resolved_redirect_permanent=resolved_redirect_permanent,
        date_refresh_failed=date_refresh_failed,
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
async def test_upsert_stores_resolved_redirect(factory: Factory) -> None:
    """An upsert round-trips the resolved redirect columns."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        url = "https://docs.example.com/en/latest/objects.inv"

        inventory = _make_inventory(
            url,
            date_fetched=now,
            date_requested=now,
            resolved_url="https://docs.example.com/en/21/objects.inv",
            resolved_redirect_permanent=False,
        )
        await store.upsert_inventory(inventory)

        stored = await store.get_inventory(url)
        assert stored is not None
        assert (
            stored.resolved_url == "https://docs.example.com/en/21/objects.inv"
        )
        assert stored.resolved_redirect_permanent is False


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
            resolved_url="https://docs.example.com/en/21/objects.inv",
            resolved_redirect_permanent=True,
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
        # The good row's resolved-redirect columns survive the skipped write
        # alongside its content: the negative-cache write clears nothing.
        assert stored == good


@pytest.mark.asyncio
async def test_failure_upsert_leaves_resolved_columns_null(
    factory: Factory,
) -> None:
    """A negative-cache row carries no resolved-redirect information.

    A failure row has no content and no resolved chain, so both columns
    stay null rather than recording whatever chain the failed attempt
    happened to walk.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        url = "https://down.example.com/objects.inv"

        await store.upsert_fetch_failure(
            _make_inventory(
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
        )

        stored = await store.get_inventory(url)
        assert stored is not None
        assert stored.resolved_url is None
        assert stored.resolved_redirect_permanent is None


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
async def test_update_refresh_outcome_rewrites_resolved_redirect(
    factory: Factory,
) -> None:
    """A refresh-outcome write replaces the stored resolved-redirect state.

    The chain an inventory redirects through can change between fetches, so
    the refresh path overwrites both columns from the chain it just walked
    rather than carrying the stored values forward.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        url = "https://docs.example.com/en/latest/objects.inv"

        seeded = _make_inventory(
            url,
            date_fetched=now - timedelta(hours=2),
            date_requested=now - timedelta(days=1),
            resolved_url="https://docs.example.com/en/20/objects.inv",
            resolved_redirect_permanent=True,
        )
        await store.upsert_inventory(seeded)

        await store.update_refresh_outcome(
            replace(
                seeded,
                date_fetched=now,
                resolved_url="https://docs.example.com/en/21/objects.inv",
                resolved_redirect_permanent=False,
            )
        )

        stored = await store.get_inventory(url)
        assert stored is not None
        assert (
            stored.resolved_url == "https://docs.example.com/en/21/objects.inv"
        )
        assert stored.resolved_redirect_permanent is False


@pytest.mark.asyncio
async def test_update_refresh_failure_keeps_the_stored_copy(
    factory: Factory,
) -> None:
    """A refresh-failure write records the attempt and changes nothing else.

    The failure must not clear the content, its validators, its
    resolved-redirect columns, or the ``date_fetched`` freshness anchor: the
    stored copy keeps serving stale and keeps reporting its true age. Only
    the failure columns and the backoff marker move.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        url = "https://docs.example.com/en/latest/objects.inv"

        seeded = _make_inventory(
            url,
            content=b"kept payload",
            date_fetched=now - timedelta(hours=2),
            date_requested=now - timedelta(days=1),
            resolved_url="https://docs.example.com/en/21/objects.inv",
            resolved_redirect_permanent=True,
        )
        await store.upsert_inventory(seeded)

        detail = "Upstream returned HTTP 500 for the inventory"
        recorded = await store.update_refresh_failure(
            url,
            now=now,
            error=detail,
            expected_date_fetched=seeded.date_fetched,
        )

        assert recorded is True
        stored = await store.get_inventory(url)
        assert stored == replace(
            seeded,
            last_fetch_status=InventoryFetchStatus.failure,
            last_fetch_error=detail,
            date_refresh_failed=now,
        )


@pytest.mark.asyncio
async def test_update_refresh_failure_skips_a_concurrently_updated_row(
    factory: Factory,
) -> None:
    """A refresh failure does not write a row that changed since it was read.

    A negative-cache row in the due list is by construction past its negative
    TTL, so a client cold miss can fetch it successfully while the refresh
    job's own fetch is still failing. Landing the failure write last would
    leave fresh content behind a ``failure`` status, a stale error, and a
    backoff marker — the cross-column shape the domain model rules out. The
    optimistic guard on ``date_fetched`` makes the late write a no-op instead.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        url = "https://docs.example.com/en/latest/objects.inv"

        # The row as the due-list read saw it.
        due_list_read = _make_inventory(
            url,
            content=None,
            etag=None,
            last_modified=None,
            date_fetched=now - timedelta(hours=2),
            date_requested=now - timedelta(days=1),
            last_fetch_status=InventoryFetchStatus.failure,
            last_fetch_error="Upstream request for the inventory timed out",
        )
        await store.upsert_inventory(due_list_read)

        # A concurrent cold miss stores good content while the refresh job's
        # fetch is still failing.
        concurrent = replace(
            due_list_read,
            content=b"fresh payload",
            etag='"fresh-etag"',
            date_fetched=now,
            date_requested=now,
            last_fetch_status=InventoryFetchStatus.success,
            last_fetch_error=None,
        )
        await store.upsert_inventory(concurrent)

        recorded = await store.update_refresh_failure(
            url,
            now=now,
            error="Upstream returned HTTP 500 for the inventory",
            expected_date_fetched=due_list_read.date_fetched,
        )

        assert recorded is False
        stored = await store.get_inventory(url)
        assert stored == concurrent


@pytest.mark.asyncio
async def test_update_refresh_failure_matches_a_never_fetched_row(
    factory: Factory,
) -> None:
    """The guard matches a row whose ``date_fetched`` is null.

    ``date_fetched = NULL`` is a real state — a row a client requested but no
    fetch has ever populated — so the guard has to compare it as a value
    rather than through SQL's three-valued equality, which would never match
    and would silently drop every such row's backoff.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        url = "https://docs.example.com/en/latest/objects.inv"

        await store.upsert_inventory(
            _make_inventory(
                url,
                content=None,
                etag=None,
                last_modified=None,
                date_fetched=None,
                date_requested=now - timedelta(days=1),
                last_fetch_status=None,
            )
        )

        recorded = await store.update_refresh_failure(
            url,
            now=now,
            error="Upstream returned HTTP 500 for the inventory",
            expected_date_fetched=None,
        )

        assert recorded is True
        stored = await store.get_inventory(url)
        assert stored is not None
        assert stored.date_refresh_failed == now


@pytest.mark.asyncio
async def test_recently_failed_inventory_is_not_due(factory: Factory) -> None:
    """A recently-failed inventory is held out of the due list for the TTL.

    Both rows below are stale and active, so only the recorded failure time
    separates them. Without the backoff a broken inventory would be selected
    on every run and — sorting stalest-fetch-first — ahead of every healthy
    one.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        ttl = timedelta(hours=1)
        active_window = timedelta(days=30)

        await store.upsert_inventory(
            _make_inventory(
                "https://backing-off.example.com/objects.inv",
                date_fetched=now - timedelta(days=1),
                date_requested=now - timedelta(hours=1),
                last_fetch_status=InventoryFetchStatus.failure,
                last_fetch_error="Upstream request timed out",
                date_refresh_failed=now - timedelta(minutes=10),
            )
        )
        await store.upsert_inventory(
            _make_inventory(
                "https://retryable.example.com/objects.inv",
                date_fetched=now - timedelta(days=1),
                date_requested=now - timedelta(hours=1),
                last_fetch_status=InventoryFetchStatus.failure,
                last_fetch_error="Upstream request timed out",
                date_refresh_failed=now - timedelta(hours=2),
            )
        )

        due = await store.get_stale_active_inventories(
            now=now, ttl=ttl, active_window=active_window
        )
        assert [inv.url for inv in due] == [
            "https://retryable.example.com/objects.inv"
        ]


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
