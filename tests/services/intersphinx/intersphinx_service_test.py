"""Tests for the IntersphinxCacheService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
import structlog
from httpx import Response

from ook.domain.intersphinx import IntersphinxInventory, InventoryFetchStatus
from ook.exceptions import InvalidInventoryUrlError, UpstreamInventoryError
from ook.factory import Factory
from ook.services import intersphinx as intersphinx_service

INVENTORY_URL = "https://docs.example.com/en/latest/objects.inv"
"""An origin ``objects.inv`` URL used across the cold-miss tests."""

INVENTORY_BODY = b"# Sphinx inventory version 2\nfake objects.inv payload"
"""A stand-in for the binary ``objects.inv`` payload."""


@pytest.mark.asyncio
async def test_cold_miss_fetches_and_stores(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A cold miss fetches the origin, stores it, and returns the record."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={
                "Content-Type": "application/octet-stream",
                "ETag": '"abc123"',
                "Last-Modified": "Wed, 09 Jul 2026 00:00:00 GMT",
            },
        )
    )
    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        inventory = await service.get_inventory(INVENTORY_URL)

    assert inventory.url == INVENTORY_URL
    assert inventory.content == INVENTORY_BODY
    assert inventory.content_type == "application/octet-stream"
    assert inventory.etag == '"abc123"'
    assert inventory.last_modified == "Wed, 09 Jul 2026 00:00:00 GMT"
    assert inventory.date_fetched is not None
    assert inventory.last_fetch_status is InventoryFetchStatus.success
    assert inventory.last_fetch_error is None
    assert respx_mock.calls.call_count == 1

    # The fetched inventory is persisted keyed by its URL.
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.content == INVENTORY_BODY


@pytest.mark.asyncio
async def test_cold_miss_logs_origin_url(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """The cold-miss fetch emits a structured log carrying the origin URL."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )
    with structlog.testing.capture_logs() as captured:
        async with factory.db_session.begin():
            service = factory.create_intersphinx_cache_service()
            await service.get_inventory(INVENTORY_URL)

    assert any(event.get("url") == INVENTORY_URL for event in captured)


@pytest.mark.asyncio
async def test_cache_hit_serves_without_refetch(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A second request serves the cached copy without a second fetch."""
    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )
    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        first = await service.get_inventory(INVENTORY_URL)
    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        second = await service.get_inventory(INVENTORY_URL)

    assert route.call_count == 1
    assert second.content == INVENTORY_BODY
    # Serving a cache hit bumps date_requested past the initial fetch.
    assert second.date_requested >= first.date_requested


@pytest.mark.asyncio
async def test_cache_hit_within_ttl_logs_hit(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A hit within the TTL is served from cache and logged as a hit."""
    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )
    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        await service.get_inventory(INVENTORY_URL)  # cold miss populates

    with structlog.testing.capture_logs() as captured:
        async with factory.db_session.begin():
            service = factory.create_intersphinx_cache_service()
            served = await service.get_inventory(INVENTORY_URL)

    # The just-fetched inventory is within the TTL, so no second upstream
    # request is made.
    assert route.call_count == 1
    assert served.content == INVENTORY_BODY
    assert any(
        event.get("cache_status") == "hit"
        and event.get("url") == INVENTORY_URL
        for event in captured
    )


@pytest.mark.asyncio
async def test_expired_inventory_served_stale_without_upstream(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """An inventory past the TTL is served stale from cache with no upstream
    request, even when the origin is down.
    """
    # The origin is "down": any request raises. The request path must never
    # call it.
    route = respx_mock.get(INVENTORY_URL).mock(
        side_effect=httpx.ConnectError("origin down")
    )
    stale_fetched = datetime.now(tz=UTC) - timedelta(hours=2)
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        await store.upsert_inventory(
            IntersphinxInventory(
                url=INVENTORY_URL,
                content=INVENTORY_BODY,
                content_type="application/octet-stream",
                etag=None,
                last_modified=None,
                date_fetched=stale_fetched,
                date_requested=stale_fetched,
                last_fetch_status=InventoryFetchStatus.success,
                last_fetch_error=None,
            )
        )

    with structlog.testing.capture_logs() as captured:
        async with factory.db_session.begin():
            service = factory.create_intersphinx_cache_service()
            served = await service.get_inventory(INVENTORY_URL)

    # The request path never touches upstream, so the down origin is
    # irrelevant and the stale copy is served without error.
    assert route.call_count == 0
    assert served.content == INVENTORY_BODY
    assert any(
        event.get("cache_status") == "stale"
        and event.get("url") == INVENTORY_URL
        for event in captured
    )


@pytest.mark.asyncio
async def test_http_url_rejected_before_fetch(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A non-HTTPS URL is rejected by the guard and never fetched."""
    http_url = "http://docs.example.com/en/latest/objects.inv"
    route = respx_mock.get(http_url).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    with pytest.raises(InvalidInventoryUrlError):
        async with factory.db_session.begin():
            service = factory.create_intersphinx_cache_service()
            await service.get_inventory(http_url)

    # The guarded URL is never fetched from upstream.
    assert route.call_count == 0

    # The guarded URL is never stored as a cache row.
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(http_url)
    assert stored is None


@pytest.mark.asyncio
async def test_private_host_rejected_before_fetch(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host resolving to a private range is rejected and never fetched."""
    private_url = "https://internal.example.com/en/latest/objects.inv"
    route = respx_mock.get(private_url).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    async def resolve_private(host: str) -> list[str]:
        return ["10.0.0.1"]

    monkeypatch.setattr(
        intersphinx_service, "_default_resolve_host", resolve_private
    )

    with pytest.raises(InvalidInventoryUrlError):
        async with factory.db_session.begin():
            service = factory.create_intersphinx_cache_service()
            await service.get_inventory(private_url)

    assert route.call_count == 0

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(private_url)
    assert stored is None


@pytest.mark.asyncio
async def test_ip_literal_link_local_rejected_before_fetch(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """An IP-literal link-local host (cloud metadata) is rejected and never
    fetched.

    The autouse conftest fixture patches the module's ``_default_resolve_host``
    to return a public address, so a rejection here proves the IP-literal
    branch — which bypasses resolution entirely — is doing the work.
    """
    metadata_url = "https://169.254.169.254/objects.inv"
    route = respx_mock.get(metadata_url).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    with pytest.raises(InvalidInventoryUrlError):
        async with factory.db_session.begin():
            service = factory.create_intersphinx_cache_service()
            await service.get_inventory(metadata_url)

    assert route.call_count == 0

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(metadata_url)
    assert stored is None


@pytest.mark.asyncio
async def test_ipv4_mapped_ipv6_literal_rejected_before_fetch(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """An IPv4-mapped IPv6 literal wrapping a link-local address is rejected.

    This covers the guard's ``ipv4_mapped`` unwrapping branch: the embedded
    IPv4 address, not the IPv6 wrapper, is what must be classified.
    """
    mapped_url = "https://[::ffff:169.254.169.254]/objects.inv"
    route = respx_mock.get(mapped_url).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    with pytest.raises(InvalidInventoryUrlError):
        async with factory.db_session.begin():
            service = factory.create_intersphinx_cache_service()
            await service.get_inventory(mapped_url)

    assert route.call_count == 0

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(mapped_url)
    assert stored is None


@pytest.mark.parametrize(
    "failure",
    [
        Response(404, content=b"not found"),
        Response(500, content=b"boom"),
        httpx.TimeoutException("timed out"),
    ],
)
@pytest.mark.asyncio
async def test_cold_miss_upstream_failure_negatively_cached(
    factory: Factory,
    respx_mock: respx.Router,
    failure: Response | httpx.TimeoutException,
) -> None:
    """A cold-miss upstream 4xx/5xx/timeout raises and is negatively cached.

    A repeat request within the negative TTL raises again without a second
    upstream call, and the stored row is a failure-status/no-content
    negative-cache entry.
    """
    if isinstance(failure, Response):
        route = respx_mock.get(INVENTORY_URL).mock(return_value=failure)
    else:
        route = respx_mock.get(INVENTORY_URL).mock(side_effect=failure)

    # No ``begin()`` wrapper: the negative-cache row the service flushes
    # must remain visible to the second request rather than being rolled
    # back, mirroring how the handler commits it on the failure path.
    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    # The second request is served from the negative cache, not upstream.
    assert route.call_count == 1

    stored = await factory.create_intersphinx_inventory_store().get_inventory(
        INVENTORY_URL
    )
    assert stored is not None
    assert stored.content is None
    assert stored.last_fetch_status is InventoryFetchStatus.failure
    assert stored.last_fetch_error is not None


@pytest.mark.asyncio
async def test_negative_cache_expiry_refetches(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A request after the negative TTL expires re-fetches the origin."""
    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )
    # Seed an expired negative-cache row: a failure fetched long ago.
    expired_fetched = datetime.now(tz=UTC) - timedelta(hours=1)
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        await store.upsert_inventory(
            IntersphinxInventory(
                url=INVENTORY_URL,
                content=None,
                content_type=None,
                etag=None,
                last_modified=None,
                date_fetched=expired_fetched,
                date_requested=expired_fetched,
                last_fetch_status=InventoryFetchStatus.failure,
                last_fetch_error="Upstream returned HTTP 500",
            )
        )

    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        served = await service.get_inventory(INVENTORY_URL)

    # The expired negative-cache row is replaced by a fresh upstream fetch.
    assert route.call_count == 1
    assert served.content == INVENTORY_BODY
    assert served.last_fetch_status is InventoryFetchStatus.success


@pytest.mark.asyncio
async def test_cold_miss_failure_logs_origin_url(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """The cold-miss failure emits a structured log with the origin URL."""
    respx_mock.get(INVENTORY_URL).mock(return_value=Response(500))

    service = factory.create_intersphinx_cache_service()
    with structlog.testing.capture_logs() as captured:
        with pytest.raises(UpstreamInventoryError):
            await service.get_inventory(INVENTORY_URL)

    assert any(
        event.get("cache_status") == "miss"
        and event.get("url") == INVENTORY_URL
        for event in captured
    )


@pytest.mark.asyncio
async def test_negative_cache_hit_logs(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A negative-cache serve emits a structured log with the origin URL."""
    respx_mock.get(INVENTORY_URL).mock(return_value=Response(500))

    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(UpstreamInventoryError):
            await service.get_inventory(INVENTORY_URL)

    assert any(
        event.get("cache_status") == "negative"
        and event.get("url") == INVENTORY_URL
        for event in captured
    )


@pytest.mark.asyncio
async def test_guard_rejection_logs_origin_url(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A guard rejection emits a structured log carrying the origin URL."""
    http_url = "http://docs.example.com/en/latest/objects.inv"
    respx_mock.get(http_url).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(InvalidInventoryUrlError):
            async with factory.db_session.begin():
                service = factory.create_intersphinx_cache_service()
                await service.get_inventory(http_url)

    assert any(event.get("url") == http_url for event in captured)


async def _seed_stale_inventory(
    factory: Factory,
    url: str,
    *,
    content: bytes | None = INVENTORY_BODY,
    etag: str | None = '"stored-etag"',
    last_modified: str | None = "Wed, 01 Jan 2025 00:00:00 GMT",
    date_fetched: datetime,
    date_requested: datetime,
    last_fetch_status: InventoryFetchStatus = InventoryFetchStatus.success,
) -> None:
    """Seed a cached inventory row for the refresh-path tests."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        await store.upsert_inventory(
            IntersphinxInventory(
                url=url,
                content=content,
                content_type="application/octet-stream",
                etag=etag,
                last_modified=last_modified,
                date_fetched=date_fetched,
                date_requested=date_requested,
                last_fetch_status=last_fetch_status,
                last_fetch_error=None,
            )
        )


@pytest.mark.asyncio
async def test_refresh_304_keeps_content_and_bumps_fetch(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A 304 revalidation keeps the stored content and bumps date_fetched.

    The conditional request carries the stored validators.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    stale_fetched = now - timedelta(hours=2)
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        date_fetched=stale_fetched,
        date_requested=now - timedelta(days=1),
    )

    seen_headers: dict[str, str] = {}

    def respond(request: httpx.Request) -> Response:
        seen_headers.update(request.headers)
        return Response(304)

    route = respx_mock.get(INVENTORY_URL).mock(side_effect=respond)

    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        summary = await service.refresh_inventories(now=now)

    assert route.call_count == 1
    assert seen_headers.get("if-none-match") == '"stored-etag"'
    assert (
        seen_headers.get("if-modified-since")
        == "Wed, 01 Jan 2025 00:00:00 GMT"
    )
    assert summary.revalidated == 1
    assert summary.refreshed == 0
    assert summary.failed == 0

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.content == INVENTORY_BODY
    assert stored.date_fetched == now
    assert stored.last_fetch_status is InventoryFetchStatus.success


@pytest.mark.asyncio
async def test_refresh_200_replaces_content_and_validators(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A 200 revalidation replaces content, etag, and last-modified."""
    now = datetime.now(tz=UTC).replace(microsecond=0)
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        content=b"old payload",
        etag='"old-etag"',
        last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )

    new_body = b"# Sphinx inventory version 2\nnew payload"
    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            200,
            content=new_body,
            headers={
                "Content-Type": "application/octet-stream",
                "ETag": '"new-etag"',
                "Last-Modified": "Fri, 10 Jul 2026 00:00:00 GMT",
            },
        )
    )

    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        summary = await service.refresh_inventories(now=now)

    assert route.call_count == 1
    assert summary.refreshed == 1
    assert summary.revalidated == 0

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.content == new_body
    assert stored.etag == '"new-etag"'
    assert stored.last_modified == "Fri, 10 Jul 2026 00:00:00 GMT"
    assert stored.date_fetched == now


@pytest.mark.asyncio
async def test_refresh_skips_inventories_outside_active_window(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """An inventory requested outside the active window is not refreshed."""
    now = datetime.now(tz=UTC).replace(microsecond=0)
    active_url = "https://active.example.com/objects.inv"
    inactive_url = "https://inactive.example.com/objects.inv"
    await _seed_stale_inventory(
        factory,
        active_url,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )
    await _seed_stale_inventory(
        factory,
        inactive_url,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=60),
    )
    active_route = respx_mock.get(active_url).mock(return_value=Response(304))
    inactive_route = respx_mock.get(inactive_url).mock(
        return_value=Response(304)
    )

    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        summary = await service.refresh_inventories(now=now)

    # Only the active inventory is revalidated; the inactive one is skipped.
    assert active_route.call_count == 1
    assert inactive_route.call_count == 0
    assert summary.considered == 1


@pytest.mark.asyncio
async def test_refresh_per_inventory_failure_does_not_abort_batch(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A per-inventory refresh failure is logged and the batch continues."""
    now = datetime.now(tz=UTC).replace(microsecond=0)
    failing_url = "https://failing.example.com/objects.inv"
    ok_url = "https://ok.example.com/objects.inv"
    await _seed_stale_inventory(
        factory,
        failing_url,
        content=b"kept payload",
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )
    await _seed_stale_inventory(
        factory,
        ok_url,
        date_fetched=now - timedelta(hours=3),
        date_requested=now - timedelta(days=1),
    )
    respx_mock.get(failing_url).mock(return_value=Response(500))
    respx_mock.get(ok_url).mock(return_value=Response(304))

    with structlog.testing.capture_logs() as captured:
        async with factory.db_session.begin():
            service = factory.create_intersphinx_cache_service()
            summary = await service.refresh_inventories(now=now)

    assert summary.failed == 1
    assert summary.revalidated == 1
    assert any(
        event.get("url") == failing_url
        and event.get("cache_status") == "refresh-failure"
        for event in captured
    )

    # The failing inventory keeps its stored content for stale serving.
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        kept = await store.get_inventory(failing_url)
    assert kept is not None
    assert kept.content == b"kept payload"
