"""Tests for the IntersphinxCacheService."""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
import structlog
from httpx import Response
from safir.database import create_async_session, create_database_engine
from sqlalchemy.exc import OperationalError
from structlog.testing import capture_logs

from ook.config import config
from ook.domain.intersphinx import IntersphinxInventory, InventoryFetchStatus
from ook.domain.redirects import MAX_REDIRECTS
from ook.exceptions import InvalidInventoryUrlError, UpstreamInventoryError
from ook.factory import Factory
from ook.services import intersphinx as intersphinx_service
from ook.storage.intersphinxstore import IntersphinxInventoryStore

INVENTORY_URL = "https://docs.example.com/en/latest/objects.inv"
"""An origin ``objects.inv`` URL used across the cold-miss tests."""

INVENTORY_BODY = b"# Sphinx inventory version 2\nfake objects.inv payload"
"""A stand-in for the binary ``objects.inv`` payload."""


def _make_service(
    factory: Factory,
    *,
    max_content_size: int | None = None,
    request_timeout: timedelta | None = None,
) -> intersphinx_service.IntersphinxCacheService:
    """Build a cache service, overriding the limits a test needs to shrink.

    Constructed directly (rather than through the factory) so a test can
    inject a limit the factory has no business exposing: a tiny
    ``max_content_size``, so an oversize-body test need not generate a 50 MB
    body, or a fraction-of-a-second ``request_timeout``, so a deadline test
    need not wait out the 30-second production budget. An override left None
    is not passed at all, so it keeps the service's own default rather than
    a copy of it restated here.
    """
    overrides: dict[str, Any] = {}
    if max_content_size is not None:
        overrides["max_content_size"] = max_content_size
    if request_timeout is not None:
        overrides["request_timeout"] = request_timeout
    return intersphinx_service.IntersphinxCacheService(
        http_client=factory.http_client,
        inventory_store=factory.create_intersphinx_inventory_store(),
        session=factory.db_session,
        ttl=timedelta(hours=1),
        negative_ttl=timedelta(hours=1),
        active_window=timedelta(days=30),
        logger=structlog.get_logger("ook"),
        **overrides,
    )


async def _assert_negative_cached(
    factory: Factory,
    url: str = INVENTORY_URL,
    *,
    error_contains: str | None = None,
    backoff_marked: bool = False,
) -> IntersphinxInventory:
    """Assert ``url`` is stored as a negative-cache row and return that row.

    The negative-cache shape is one invariant — no content, a ``failure``
    status, a stored error detail, and a backoff marker set by the refresh
    path and only by the refresh path — so asserting it is all-or-nothing
    here rather than each test spelling out whichever part of it that test
    happened to think of. ``error_contains`` adds the detail the caller's
    own failure mode is identified by; the row is returned for any further
    assertion specific to the caller.

    ``backoff_marked`` names which path wrote the row. A request-path
    failure dates itself with ``date_fetched`` and must leave
    ``date_refresh_failed`` null; a refresh failure must set it, since that
    marker is the only thing holding a broken inventory back to the normal
    refresh cadence. Defaulting to the request path and making the refresh
    callers say so keeps that split asserted at every call site instead of
    at none of them.
    """
    stored = await factory.create_intersphinx_inventory_store().get_inventory(
        url
    )
    assert stored is not None
    assert stored.content is None
    assert stored.last_fetch_status is InventoryFetchStatus.failure
    assert stored.last_fetch_error is not None
    if error_contains is not None:
        assert error_contains in stored.last_fetch_error
    if backoff_marked:
        assert stored.date_refresh_failed is not None
    else:
        assert stored.date_refresh_failed is None
    return stored


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
                date_refresh_failed=None,
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


@pytest.mark.asyncio
async def test_unparseable_url_rejected_before_resolution(
    factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A URL that does not parse is refused without a DNS lookup.

    ``urlsplit`` accepts this URL's bogus port, so before the guard parsed
    the URL with httpx as well the host was resolved — every repeat paying
    for the lookup again — only for httpx to then refuse the URL.
    """
    resolved: list[str] = []

    async def resolve(host: str) -> list[str]:
        resolved.append(host)
        return ["93.184.216.34"]

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    with pytest.raises(InvalidInventoryUrlError, match="could not be parsed"):
        async with factory.db_session.begin():
            service = factory.create_intersphinx_cache_service()
            await service.get_inventory(
                "https://docs.example.com:notaport/objects.inv"
            )

    assert resolved == []


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

    await _assert_negative_cached(factory)


@pytest.mark.asyncio
async def test_cold_miss_oversized_body_negatively_cached(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A cold-miss body exceeding the size cap raises and is negatively cached.

    The oversized body is streamed without a ``Content-Length`` (chunked), so
    the abort comes from the streamed-size cap rather than the upfront
    length check. A repeat request within the negative TTL is served from the
    negative cache without re-contacting upstream.
    """

    async def oversized_body() -> AsyncIterator[bytes]:
        for _ in range(4):
            yield b"x" * 64  # 256 bytes total, well over the 64-byte cap

    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=oversized_body())
    )

    # No ``begin()`` wrapper so the negative-cache row stays visible to the
    # second request, mirroring how the handler commits the failure path.
    service = _make_service(factory, max_content_size=64)
    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    # The second request is served from the negative cache, not upstream.
    assert route.call_count == 1

    await _assert_negative_cached(factory, error_contains="cap")


@pytest.mark.asyncio
async def test_cold_miss_content_length_over_cap_negatively_cached(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """An upfront ``Content-Length`` over the cap aborts without the body.

    The declared length is over the cap while the actual body is tiny (under
    the cap), so a negative-cache failure proves the abort came from the
    upfront length check rather than from streaming the body.
    """
    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            200,
            headers={"Content-Length": "1000000"},
            content=b"tiny",
        )
    )

    service = _make_service(factory, max_content_size=64)
    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    assert route.call_count == 1

    await _assert_negative_cached(factory, error_contains="cap")


@pytest.mark.asyncio
async def test_cold_miss_empty_body_negatively_cached(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A terminal ``200`` carrying no bytes is an upstream failure.

    ``raise_for_status()`` waves an empty ``200`` through, so a CDN edge
    glitch or a truncated object used to be stored as a ``success`` row
    holding ``b""``: a permanent cache hit serving nothing that no later
    cold miss, no negative-cache expiry, and no failure upsert could
    displace, because every one of those reads the row as content-bearing.
    """
    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=b"")
    )

    # No ``begin()`` wrapper so the negative-cache row stays visible,
    # mirroring how the handler commits the failure path.
    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    assert route.call_count == 1

    await _assert_negative_cached(factory, error_contains="empty")


@pytest.mark.asyncio
async def test_cold_miss_no_content_status_negatively_cached(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A ``204 No Content`` terminal is an upstream failure, not a hit.

    A 204 is neither a redirect nor a ``304``, so it reaches the terminal
    branch as a success status with an empty body — the same unservable
    content-less ``success`` row an empty ``200`` would leave behind.
    """
    route = respx_mock.get(INVENTORY_URL).mock(return_value=Response(204))

    # No ``begin()`` wrapper so the negative-cache row stays visible,
    # mirroring how the handler commits the failure path.
    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    assert route.call_count == 1

    await _assert_negative_cached(factory, error_contains="empty")


@pytest.mark.asyncio
async def test_cold_miss_304_negatively_cached(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A ``304`` answering a cold miss is an upstream failure, not a hit.

    A cold miss has nothing to revalidate — no stored entity-tag, no stored
    modification date — so its request goes out unconditional and a ``304``
    answers a question it never asked. Trusting it would store a
    content-None row under a ``success`` status: unservable, invisible to
    the negative-cache window, and, because every later read tests
    ``content is not None``, never displaced by another cold miss.

    This is the shape that exercises the terminal check with the empty
    header dict the request path builds. The refresh path reaches the same
    branch, but always through a row that had *some* headers to consider,
    so an ``_has_validator`` that read an empty mapping as conditional would
    pass every one of those tests.
    """
    route = respx_mock.get(INVENTORY_URL).mock(return_value=Response(304))

    # No ``begin()`` wrapper so the negative-cache row stays visible,
    # mirroring how the handler commits the failure path.
    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError, match="unconditional"):
        await service.get_inventory(INVENTORY_URL)

    assert route.call_count == 1

    await _assert_negative_cached(factory, error_contains="unconditional")


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
                date_refresh_failed=None,
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


@pytest.mark.asyncio
async def test_cold_miss_follows_cross_host_redirect_chain(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A cold miss behind a three-hop cross-host 302 chain resolves.

    The chain mirrors the real SQLAlchemy inventory: it leaves the origin
    host and comes back. The terminal inventory is stored under the
    originally requested URL, which is the cache key clients ask for.
    """
    hop_1 = "https://www.example.com/docs/latest/objects.inv"
    hop_2 = "https://docs.example.com/21/objects.inv"
    terminal = "https://docs.example.com/en/21/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": hop_1})
    )
    respx_mock.get(hop_1).mock(
        return_value=Response(302, headers={"Location": hop_2})
    )
    respx_mock.get(hop_2).mock(
        return_value=Response(302, headers={"Location": terminal})
    )
    terminal_route = respx_mock.get(terminal).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        inventory = await service.get_inventory(INVENTORY_URL)

    assert terminal_route.call_count == 1
    assert inventory.content == INVENTORY_BODY
    assert inventory.last_fetch_status is InventoryFetchStatus.success

    # The terminal content is cached under the requested URL, not the
    # terminal one, so the next request for the same URL is a hit.
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.content == INVENTORY_BODY


@pytest.mark.asyncio
async def test_cold_miss_stores_temporary_redirect_chain(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """An all-302 chain records its terminal URL as a temporary redirect.

    This is the SQLAlchemy shape: the ``latest`` alias legitimately moves,
    so the resolved URL is recorded but not marked permanent.
    """
    hop_1 = "https://www.example.com/docs/latest/objects.inv"
    terminal = "https://docs.example.com/en/21/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": hop_1})
    )
    respx_mock.get(hop_1).mock(
        return_value=Response(302, headers={"Location": terminal})
    )
    respx_mock.get(terminal).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        await service.get_inventory(INVENTORY_URL)

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.resolved_url == terminal
    assert stored.resolved_redirect_permanent is False


@pytest.mark.asyncio
async def test_cold_miss_stores_permanent_redirect_chain(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A chain of only 301 and 308 hops is recorded as permanent.

    This is the pydantic shape: the requested URL itself has moved, which
    is the case worth surfacing to a doc author.
    """
    hop_1 = "https://www.example.com/docs/latest/objects.inv"
    terminal = "https://www.example.com/docs/validation/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(301, headers={"Location": hop_1})
    )
    respx_mock.get(hop_1).mock(
        return_value=Response(308, headers={"Location": terminal})
    )
    respx_mock.get(terminal).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        await service.get_inventory(INVENTORY_URL)

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.resolved_url == terminal
    assert stored.resolved_redirect_permanent is True


@pytest.mark.asyncio
async def test_cold_miss_without_redirect_leaves_resolved_columns_null(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A chain-free fetch records no resolved URL and no permanence.

    Null, not the requested URL, so a reader can tell "did not redirect"
    from "redirected somewhere".
    """
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        inventory = await service.get_inventory(INVENTORY_URL)

    assert inventory.resolved_url is None
    assert inventory.resolved_redirect_permanent is None

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.resolved_url is None
    assert stored.resolved_redirect_permanent is None


@pytest.mark.asyncio
async def test_location_fragment_stripped_from_resolved_url(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A fragment on the final ``Location`` never reaches ``resolved_url``.

    A fragment is display metadata for a document, never part of the
    inventory resource's identity, and the stored value is served verbatim
    as the permanent-redirect header — a doc author told to paste
    ``objects.inv#moved`` into ``intersphinx_mapping`` would be told to
    configure a URL that names nothing.
    """
    terminal = "https://www.example.com/docs/validation/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(301, headers={"Location": f"{terminal}#moved"})
    )
    respx_mock.get(terminal).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        inventory = await service.get_inventory(INVENTORY_URL)

    assert inventory.resolved_url == terminal

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.resolved_url == terminal


@pytest.mark.asyncio
async def test_relative_location_joins_against_current_hop(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A relative ``Location`` resolves against the hop that sent it.

    The first hop crosses to another host, which then answers with a
    relative ``Location``. Joining that against the originally requested
    URL would land on the wrong host and path entirely, so the correct
    target resolving proves the join uses the current hop.
    """
    hop_1 = "https://www.example.com/docs/latest/objects.inv"
    terminal = "https://www.example.com/docs/21/objects.inv"
    wrong = "https://docs.example.com/en/21/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": hop_1})
    )
    respx_mock.get(hop_1).mock(
        return_value=Response(302, headers={"Location": "../21/objects.inv"})
    )
    terminal_route = respx_mock.get(terminal).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )
    wrong_route = respx_mock.get(wrong).mock(
        return_value=Response(200, content=b"wrong host")
    )

    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        inventory = await service.get_inventory(INVENTORY_URL)

    assert terminal_route.call_count == 1
    assert wrong_route.call_count == 0
    assert inventory.content == INVENTORY_BODY


@pytest.mark.asyncio
async def test_repeated_location_headers_follow_the_first(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A hop carrying two ``Location`` headers follows the first value.

    ``headers.get("Location")`` concatenates the pair with ``", "``, and
    joining that against the current hop percent-encodes it into one URL
    naming neither target — one that keeps the origin's host, so it
    passes the SSRF guard, is fetched, and 404s. That would negatively
    cache a working inventory and 502 every documenteer build for the
    negative-TTL window.
    """
    first = "https://www.example.com/docs/latest/objects.inv"
    second = "https://other.example.com/docs/latest/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            301,
            headers=[("Location", first), ("Location", second)],
        )
    )
    first_route = respx_mock.get(first).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )
    second_route = respx_mock.get(second).mock(
        return_value=Response(200, content=b"the other target")
    )

    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        inventory = await service.get_inventory(INVENTORY_URL)

    assert first_route.call_count == 1
    assert second_route.call_count == 0
    assert inventory.content == INVENTORY_BODY
    assert inventory.resolved_url == first


@pytest.mark.asyncio
async def test_redirect_hop_to_private_address_rejected(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A redirect to a private address is rejected as an upstream failure.

    The requested URL is public and valid, so this is upstream misbehaving
    rather than a bad client request: it surfaces as an
    `UpstreamInventoryError` (502) and is negatively cached, unlike a guard
    rejection of the originally requested URL, which is a 400.
    """
    internal = "https://internal.example.com/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": internal})
    )
    internal_route = respx_mock.get(internal).mock(
        return_value=Response(200, content=b"internal secrets")
    )

    async def resolve(host: str) -> list[str]:
        return (
            ["10.0.0.1"]
            if host == "internal.example.com"
            else ["93.184.216.34"]
        )

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    # No ``begin()`` wrapper so the negative-cache row stays visible to the
    # second request, mirroring how the handler commits the failure path.
    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    # The rejected hop is never fetched.
    assert internal_route.call_count == 0

    await _assert_negative_cached(factory, error_contains="redirected")


@pytest.mark.asyncio
async def test_rejected_redirect_hop_detail_omits_resolution(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected hop's served detail names no host and no address.

    The detail is stored on the negative-cache row and replayed in the 502
    body to every client for the whole negative-TTL window, so an origin
    that redirects to an internal cluster name must not turn Ook's own DNS
    view into a client-visible oracle.
    """
    internal = "https://internal.example.com/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": internal})
    )
    respx_mock.get(internal).mock(
        return_value=Response(200, content=b"internal secrets")
    )

    async def resolve(host: str) -> list[str]:
        return (
            ["10.0.0.1"]
            if host == "internal.example.com"
            else ["93.184.216.34"]
        )

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    # No ``begin()`` wrapper so the negative-cache row stays visible,
    # mirroring how the handler commits the failure path.
    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError) as raised:
        await service.get_inventory(INVENTORY_URL)

    detail = "Upstream redirected the inventory to a disallowed target"
    assert str(raised.value) == detail

    stored = await _assert_negative_cached(factory)
    assert stored.last_fetch_error == detail


@pytest.mark.asyncio
async def test_rejected_redirect_hop_logs_the_specific_reason(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rejection an operator needs is logged, not served.

    One record has to carry both halves: the URL the client asked for —
    which is all the generic served detail and the negative-cache row name —
    and the hop that was refused with the guard's specific reason.
    """
    internal = "https://internal.example.com/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": internal})
    )
    respx_mock.get(internal).mock(
        return_value=Response(200, content=b"internal secrets")
    )

    async def resolve(host: str) -> list[str]:
        return (
            ["10.0.0.1"]
            if host == "internal.example.com"
            else ["93.184.216.34"]
        )

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    service = factory.create_intersphinx_cache_service()
    with capture_logs() as logs, pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    hop_records = [record for record in logs if record.get("hop_url")]
    assert len(hop_records) == 1
    record = hop_records[0]
    assert record["url"] == INVENTORY_URL
    assert record["hop_url"] == internal
    assert "10.0.0.1" in record["reason"]
    assert record["log_level"] == "warning"


@pytest.mark.asyncio
async def test_redirect_hop_dns_failure_negatively_cached(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A redirect hop whose host fails to resolve is an upstream failure.

    ``socket.gaierror`` is not an ``httpx.HTTPError``, so an unconverted
    resolution failure would escape the cold-miss path's handler as a 500
    with no negative-cache row, leaving the origin chain to be re-walked on
    every request. It must land in the same 502-and-negatively-cache
    treatment as any other hop the guard refuses.
    """
    retired = "https://retired.example.com/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": retired})
    )
    retired_route = respx_mock.get(retired).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    async def resolve(host: str) -> list[str]:
        if host == "retired.example.com":
            raise socket.gaierror(
                socket.EAI_NONAME, "Name or service not known"
            )
        return ["93.184.216.34"]

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    # No ``begin()`` wrapper so the negative-cache row stays visible,
    # mirroring how the handler commits the failure path.
    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    # The unresolvable hop is never fetched.
    assert retired_route.call_count == 0

    await _assert_negative_cached(factory, error_contains="redirected")


@pytest.mark.asyncio
async def test_malformed_redirect_location_negatively_cached(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A ``Location`` that is not a parseable URL is an upstream failure.

    httpx builds a redirect request for any 3xx carrying a ``Location``
    even with ``follow_redirects=False``, so a non-numeric port is reported
    as an ``httpx.RemoteProtocolError`` from the request itself rather than
    reaching this service's own join. Either way it must be a negatively
    cached 502, never an unhandled exception.
    """
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            302, headers={"Location": "https://docs.example.com:notaport/x"}
        )
    )

    # No ``begin()`` wrapper so the negative-cache row stays visible,
    # mirroring how the handler commits the failure path.
    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    await _assert_negative_cached(factory)


@pytest.mark.asyncio
async def test_overlong_redirect_location_negatively_cached(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A ``Location`` httpx itself cannot join is a negatively cached 502.

    httpx builds a redirect request for every 3xx carrying a ``Location``
    even under ``follow_redirects=False``, and its join of a relative target
    that only overflows the URL length limit once joined raises
    ``httpx.InvalidURL`` from inside ``client.stream()`` — before any
    response is returned, so this service's own join never sees it.
    ``httpx.InvalidURL`` is not an ``httpx.HTTPError``, so an unconverted
    one escapes the cold-miss handler as an unhandled 500 that caches
    nothing and re-walks the chain on every repeat.
    """
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": "/" + "a" * 65530})
    )

    # No ``begin()`` wrapper so the negative-cache row stays visible,
    # mirroring how the handler commits the failure path.
    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    await _assert_negative_cached(factory, error_contains="malformed URL")


def test_join_redirect_url_rejects_unparseable_location() -> None:
    """The redirect join converts an unparseable target to an upstream error.

    A ``Location`` httpx itself refuses never reaches this join, so this is
    the backstop for the residue httpx does accept — a relative target that
    only overflows the URL length limit once joined, say — and for httpx
    ever moving that validation. ``httpx.InvalidURL`` is not an
    ``httpx.HTTPError``, so without the conversion such a target would
    escape both fetch paths' handlers.
    """
    with pytest.raises(httpx.HTTPError, match="malformed URL"):
        intersphinx_service._join_redirect_url(
            INVENTORY_URL, "https://docs.example.com:notaport/x"
        )


@pytest.mark.asyncio
async def test_requested_url_dns_failure_negatively_cached(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A requested URL whose host will not resolve is an upstream failure.

    A well-formed host that does not resolve right now is not the client's
    mistake — a resolver blip would otherwise tell a doc author their
    ``intersphinx_mapping`` entry is bad — so it is the 502 an upstream
    failure gets rather than the 400 a URL the client can fix gets, and it
    is negatively cached like every other upstream failure.
    """
    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    async def resolve(host: str) -> list[str]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    # No ``begin()`` wrapper so the negative-cache row stays visible,
    # mirroring how the handler commits the failure path.
    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    assert route.call_count == 0

    await _assert_negative_cached(factory, error_contains="resolved")


@pytest.mark.asyncio
async def test_requested_url_dns_failure_served_from_negative_cache(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeat request for an unresolvable host pays for no second lookup.

    The 400 this used to be escaped `_fetch_and_store`'s ``httpx.HTTPError``
    handler, so nothing was cached and every retry re-paid a full
    ``ndots``-expanded lookup in a cluster with no caching resolver.
    """
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )
    lookups: list[str] = []

    async def resolve(host: str) -> list[str]:
        lookups.append(host)
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    # No ``begin()`` wrapper: the negative-cache row must stay visible to
    # the second request rather than being rolled back.
    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)
    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    assert lookups == ["docs.example.com"]


@pytest.mark.asyncio
async def test_requested_url_dns_failure_detail_omits_the_resolver_text(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stored and served detail says nothing the resolver said.

    This detail is not a transient message to one client: it is written to
    the negative-cache row and replayed in the 502 body of every request
    inside the negative-TTL window, so it is scrubbed for the same reason
    a refused hop's and a rebound stored URL's are.
    """
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    async def resolve(host: str) -> list[str]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError) as excinfo:
        await service.get_inventory(INVENTORY_URL)

    assert "Name or service not known" not in str(excinfo.value)
    stored = await _assert_negative_cached(factory)
    # Re-asserted only for the narrowing the helper's own assertion cannot
    # carry back across the call.
    assert stored.last_fetch_error is not None
    assert "Name or service not known" not in stored.last_fetch_error


@pytest.mark.asyncio
async def test_requested_url_dns_failure_logs_the_specific_reason(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The resolver's reason scrubbed off the row is logged instead."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    async def resolve(host: str) -> list[str]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    service = factory.create_intersphinx_cache_service()
    with capture_logs() as logs, pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    reasons = [
        record
        for record in logs
        if "Name or service not known" in str(record.get("reason", ""))
    ]
    assert len(reasons) == 1
    assert reasons[0]["url"] == INVENTORY_URL
    assert reasons[0]["log_level"] == "warning"


@pytest.mark.asyncio
async def test_requested_url_without_addresses_negatively_cached(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host that resolves to nothing is the same failure as one that
    raises.

    An empty answer is an absence of an answer, not a fact about the
    client's URL, so it belongs on the 502 side of the split with the
    resolver errors rather than with the rejections (bad scheme, non-public
    address) that name something the client asked for and can fix.
    """
    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    async def resolve(host: str) -> list[str]:
        return []

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    # No ``begin()`` wrapper so the negative-cache row stays visible,
    # mirroring how the handler commits the failure path.
    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError):
        await service.get_inventory(INVENTORY_URL)

    assert route.call_count == 0

    await _assert_negative_cached(factory, error_contains="resolved")


@pytest.mark.asyncio
async def test_chain_at_the_hop_cap_resolves(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A chain of exactly ``MAX_REDIRECTS`` hops resolves and is stored.

    The loop tests cover the cap's failing side; this is its passing side.
    Every other redirect test either walks one to three hops or overshoots
    the cap outright, so an off-by-one in ``len(hops) >= MAX_REDIRECTS``
    would report a legitimate 20-hop inventory as ``Exceeded 20 redirects``
    — negatively cached, and 502 for the whole negative TTL — with the rest
    of the suite still green.
    """
    chain = [
        f"https://docs.example.com/hop{hop}/objects.inv"
        for hop in range(MAX_REDIRECTS)
    ]
    terminal = "https://docs.example.com/en/21/objects.inv"
    for index, hop_url in enumerate(chain):
        next_url = chain[index + 1] if index + 1 < len(chain) else terminal
        respx_mock.get(hop_url).mock(
            return_value=Response(301, headers={"Location": next_url})
        )
    terminal_route = respx_mock.get(terminal).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        inventory = await service.get_inventory(chain[0])

    assert inventory.content == INVENTORY_BODY
    assert inventory.resolved_url == terminal
    assert inventory.resolved_redirect_permanent is True
    assert terminal_route.call_count == 1
    # One request per allowed hop, plus the terminal the last hop reaches.
    assert respx_mock.calls.call_count == MAX_REDIRECTS + 1


@pytest.mark.asyncio
async def test_redirect_loop_stops_at_hop_cap(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A redirect loop stops at the hop cap instead of spinning forever."""
    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": INVENTORY_URL})
    )

    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError, match="Exceeded 20 redirects"):
        await service.get_inventory(INVENTORY_URL)

    # One request per allowed hop, plus the one that trips the cap.
    assert route.call_count == 21

    stored = await _assert_negative_cached(factory)
    assert stored.last_fetch_error == "Exceeded 20 redirects"


@pytest.mark.asyncio
async def test_hop_cap_reported_before_touching_the_next_target(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hop cap is reported without inspecting the target it rules out.

    The hop that trips the cap points at a host that cannot be resolved, so
    a cap enforced only after joining and guarding that target would report
    the guard's failure — or, worse, whatever the resolver raised — for a
    URL this fetch was never going to request. The failure a client sees
    must depend on the hop count alone.
    """
    poison = "https://poison.example.com/objects.inv"
    calls = 0

    def respond(request: httpx.Request) -> Response:
        nonlocal calls
        calls += 1
        location = poison if calls > MAX_REDIRECTS else INVENTORY_URL
        return Response(302, headers={"Location": location})

    route = respx_mock.get(INVENTORY_URL).mock(side_effect=respond)

    resolved_hosts: list[str] = []

    async def resolve(host: str) -> list[str]:
        resolved_hosts.append(host)
        if host == "poison.example.com":
            raise socket.gaierror(
                socket.EAI_NONAME, "Name or service not known"
            )
        return ["93.184.216.34"]

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError, match="Exceeded 20 redirects"):
        await service.get_inventory(INVENTORY_URL)

    # One request per allowed hop, plus the one that trips the cap.
    assert route.call_count == 21
    # The ruled-out target is never joined, guarded, or resolved.
    assert "poison.example.com" not in resolved_hosts

    stored = await _assert_negative_cached(factory)
    assert stored.last_fetch_error == "Exceeded 20 redirects"


@pytest.mark.asyncio
async def test_redirect_hop_bodies_not_counted_against_size_cap(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """Redirect-hop bodies never count against the size cap.

    Each hop carries a body larger than the cap on its own, yet the chain
    resolves because only the terminal response's body is read and
    measured.
    """
    hop_1 = "https://www.example.com/docs/latest/objects.inv"
    terminal = "https://docs.example.com/en/21/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            302, headers={"Location": hop_1}, content=b"x" * 200
        )
    )
    respx_mock.get(hop_1).mock(
        return_value=Response(
            302, headers={"Location": terminal}, content=b"y" * 200
        )
    )
    respx_mock.get(terminal).mock(return_value=Response(200, content=b"small"))

    service = _make_service(factory, max_content_size=64)
    async with factory.db_session.begin():
        inventory = await service.get_inventory(INVENTORY_URL)

    assert inventory.content == b"small"
    assert inventory.last_fetch_status is InventoryFetchStatus.success


@pytest.mark.asyncio
async def test_small_redirect_hop_body_is_drained(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A small redirect-hop body is read to the end and discarded.

    An HTTP/1.1 connection whose response body is left unread cannot go back
    in the pool, so a chain that returns to a host it already visited would
    open a fresh TCP+TLS connection for each hop. Reading the hop's body —
    a couple of hundred bytes of boilerplate in practice — is what keeps the
    connection reusable.
    """
    terminal = "https://docs.example.com/en/21/objects.inv"
    drained = False

    async def hop_body() -> AsyncIterator[bytes]:
        nonlocal drained
        yield b"<html>moved</html>"
        drained = True

    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            302, headers={"Location": terminal}, content=hop_body()
        )
    )
    respx_mock.get(terminal).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        inventory = await service.get_inventory(INVENTORY_URL)

    assert drained is True
    assert inventory.content == INVENTORY_BODY


@pytest.mark.asyncio
async def test_oversized_redirect_hop_body_is_abandoned(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A redirect hop dumping a huge body is abandoned, not drained.

    Draining a hop buys back one pooled connection, which is only worth a
    bounded read: a hop whose body runs past the drain cap is dropped
    (closing its connection) rather than read to the end. The abandoned
    bytes are still not measured against ``max_content_size``, which
    applies to the terminal response alone.
    """
    terminal = "https://docs.example.com/en/21/objects.inv"
    chunk_size = 1024
    drain_limit = intersphinx_service._HOP_DRAIN_LIMIT
    total_chunks = 4 * drain_limit // chunk_size
    yielded = 0

    async def hop_body() -> AsyncIterator[bytes]:
        nonlocal yielded
        for _ in range(total_chunks):
            yielded += 1
            yield b"x" * chunk_size

    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            302, headers={"Location": terminal}, content=hop_body()
        )
    )
    respx_mock.get(terminal).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    service = _make_service(factory, max_content_size=len(INVENTORY_BODY))
    async with factory.db_session.begin():
        inventory = await service.get_inventory(INVENTORY_URL)

    assert yielded < total_chunks
    assert inventory.content == INVENTORY_BODY


@pytest.mark.asyncio
async def test_repeated_host_in_a_chain_is_resolved_once(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host revisited within one chain is guard-resolved only once.

    The guard's resolution is the expensive half of a hop: the cluster has
    no caching resolver and ``ndots`` search expansion multiplies each
    external-name lookup. A chain that leaves a host and comes back — the
    motivating SQLAlchemy shape — pays for that host once, while the new
    host in the chain is still guarded.
    """
    hop = "https://www.example.com/docs/latest/objects.inv"
    terminal = "https://docs.example.com/en/21/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": hop})
    )
    respx_mock.get(hop).mock(
        return_value=Response(302, headers={"Location": terminal})
    )
    respx_mock.get(terminal).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )
    resolutions: list[str] = []

    async def resolve(host: str) -> list[str]:
        resolutions.append(host)
        return ["93.184.216.34"]

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        inventory = await service.get_inventory(INVENTORY_URL)

    assert inventory.content == INVENTORY_BODY
    # The requested host is resolved by the pre-fetch guard and not again
    # when the chain returns to it.
    assert resolutions == ["docs.example.com", "www.example.com"]


@pytest.mark.asyncio
async def test_validated_hosts_are_not_remembered_across_fetches(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard-validated hosts are forgotten when the fetch ends.

    Skipping a repeat resolution is safe within one chain, seconds after
    the host was validated; remembering it for the life of the service
    would instead be an unbounded cache of "this host was public once",
    which is exactly the check DNS rebinding is meant to defeat.
    """
    other_url = "https://docs.example.com/en/v1/objects.inv"
    hop = "https://cdn.example.com/latest/objects.inv"
    other_hop = "https://cdn.example.com/v1/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": hop})
    )
    respx_mock.get(other_url).mock(
        return_value=Response(302, headers={"Location": other_hop})
    )
    respx_mock.get(hop).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )
    respx_mock.get(other_hop).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )
    resolutions: list[str] = []

    async def resolve(host: str) -> list[str]:
        resolutions.append(host)
        return ["93.184.216.34"]

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    async with factory.db_session.begin():
        service = factory.create_intersphinx_cache_service()
        await service.get_inventory(INVENTORY_URL)
        await service.get_inventory(other_url)

    # The second fetch re-resolves the hop host its own chain reaches.
    assert resolutions.count("cdn.example.com") == 2


@pytest.mark.asyncio
async def test_repeated_host_hop_is_still_scheme_checked(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A hop back to a validated host over ``http`` is still rejected.

    The dedup skips the *resolution*, not the guard. TLS hostname
    verification on the https-only fetch is what backstops rebinding here
    in place of pinning the validated address, so the scheme check has to
    run on every hop however familiar its host.
    """
    hop = "http://docs.example.com/en/21/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": hop})
    )
    hop_route = respx_mock.get(hop).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    # No ``begin()`` wrapper so the negative-cache row stays visible,
    # mirroring how the handler commits the failure path.
    service = factory.create_intersphinx_cache_service()
    with pytest.raises(UpstreamInventoryError, match="disallowed target"):
        await service.get_inventory(INVENTORY_URL)

    assert hop_route.call_count == 0


@pytest.mark.asyncio
async def test_slow_redirect_chain_stops_at_the_time_budget(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A chain dribbled out past the fetch budget gives up on the budget.

    Every hop answers well inside its own per-hop timeout, so only a
    whole-chain deadline can stop the chain: without one, an origin that
    replies just under the timeout on each of the allowed hops holds the
    cold-miss path — and the request's open DB session — for the hop cap
    times the per-request timeout.
    """

    async def slow_redirect(request: httpx.Request) -> Response:
        await asyncio.sleep(0.05)
        return Response(302, headers={"Location": INVENTORY_URL})

    route = respx_mock.get(INVENTORY_URL).mock(side_effect=slow_redirect)

    # No ``begin()`` wrapper so the negative-cache row stays visible,
    # mirroring how the handler commits the failure path.
    service = _make_service(factory, request_timeout=timedelta(seconds=0.2))
    with pytest.raises(UpstreamInventoryError, match="time budget"):
        await service.get_inventory(INVENTORY_URL)

    # The budget ended the chain well before the hop cap would have.
    assert route.call_count < MAX_REDIRECTS

    await _assert_negative_cached(factory, error_contains="time budget")


@pytest.mark.asyncio
async def test_terminal_body_read_bounded_by_the_time_budget(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A terminal body dribbled out past the fetch budget is abandoned.

    The read timeout bounds the wait for one chunk, not the whole body, so
    an origin trickling bytes indefinitely is only stopped by the budget
    covering the terminal read as well as the hops.
    """

    async def dribbled_body() -> AsyncIterator[bytes]:
        for _ in range(100):
            await asyncio.sleep(0.02)
            yield b"x"

    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=dribbled_body())
    )

    # No ``begin()`` wrapper so the negative-cache row stays visible,
    # mirroring how the handler commits the failure path.
    service = _make_service(factory, request_timeout=timedelta(seconds=0.2))
    with pytest.raises(UpstreamInventoryError, match="time budget"):
        await service.get_inventory(INVENTORY_URL)

    await _assert_negative_cached(factory, error_contains="time budget")


@pytest.mark.asyncio
async def test_redirect_guard_resolution_bounded_by_the_time_budget(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hop whose host resolution hangs is cut off by the fetch budget.

    The SSRF guard's resolver carries no timeout of its own, so a hop
    pointing at a host whose DNS lookup never answers would stall the fetch
    — and the request's open DB session — outside every other bound the
    chain has.
    """
    hop = "https://slow-dns.example.com/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": hop})
    )
    hop_route = respx_mock.get(hop).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    async def resolve(host: str) -> list[str]:
        if host == "slow-dns.example.com":
            await asyncio.sleep(30)
        return ["93.184.216.34"]

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    # No ``begin()`` wrapper so the negative-cache row stays visible,
    # mirroring how the handler commits the failure path.
    service = _make_service(factory, request_timeout=timedelta(seconds=0.2))
    with pytest.raises(UpstreamInventoryError, match="time budget"):
        await service.get_inventory(INVENTORY_URL)

    # The hop whose guard never finished is never fetched.
    assert hop_route.call_count == 0

    await _assert_negative_cached(factory, error_contains="time budget")


@pytest.mark.asyncio
async def test_fast_redirect_chain_resolves_within_the_time_budget(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A prompt multi-hop chain still resolves under a finite budget.

    Each hop gets only the time left in the budget rather than a fresh
    per-request timeout, so the shrinking allowance must not trip a chain
    that answers quickly.
    """
    hop_1 = "https://www.example.com/docs/latest/objects.inv"
    terminal = "https://docs.example.com/en/21/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": hop_1})
    )
    respx_mock.get(hop_1).mock(
        return_value=Response(302, headers={"Location": terminal})
    )
    respx_mock.get(terminal).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    service = _make_service(factory, request_timeout=timedelta(seconds=5))
    async with factory.db_session.begin():
        inventory = await service.get_inventory(INVENTORY_URL)

    assert inventory.content == INVENTORY_BODY
    assert inventory.last_fetch_status is InventoryFetchStatus.success
    assert inventory.resolved_url == terminal


@pytest.mark.asyncio
async def test_stalled_response_headers_stop_at_the_time_budget(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """An origin that stalls mid-response cannot outlast the fetch budget.

    The budget checks between hops and between body chunks only run when a
    hop completes, and the per-call httpx read timeout is re-armed on every
    socket read while the response headers arrive. Only cancelling the fetch
    at the deadline bounds an origin that stalls inside a single hop — the
    request's DB session is held open for as long as it does.
    """

    async def stalled(request: httpx.Request) -> Response:
        await asyncio.sleep(3)
        return Response(200, content=INVENTORY_BODY)

    respx_mock.get(INVENTORY_URL).mock(side_effect=stalled)

    # No ``begin()`` wrapper so the negative-cache row stays visible,
    # mirroring how the handler commits the failure path.
    service = _make_service(factory, request_timeout=timedelta(seconds=0.2))
    start = time.monotonic()
    with pytest.raises(UpstreamInventoryError, match="time budget"):
        await service.get_inventory(INVENTORY_URL)
    assert time.monotonic() - start < 1.5

    await _assert_negative_cached(factory, error_contains="time budget")


@pytest.mark.asyncio
async def test_requested_url_guard_bounded_by_the_time_budget(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung resolver on the requested URL is cut off by the fetch budget.

    The requested URL's guard runs before the first hop, so unless the
    budget covers it too a hung resolver ladder stalls the request — and its
    open DB session — outside every bound the fetch has.
    """
    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    async def resolve(host: str) -> list[str]:
        await asyncio.sleep(3)
        return ["93.184.216.34"]

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    # No ``begin()`` wrapper so the negative-cache row stays visible,
    # mirroring how the handler commits the failure path.
    service = _make_service(factory, request_timeout=timedelta(seconds=0.2))
    start = time.monotonic()
    with pytest.raises(UpstreamInventoryError, match="time budget"):
        await service.get_inventory(INVENTORY_URL)
    assert time.monotonic() - start < 1.5

    # The URL whose guard never finished is never fetched.
    assert route.call_count == 0

    await _assert_negative_cached(factory, error_contains="time budget")


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
    resolved_url: str | None = None,
    resolved_redirect_permanent: bool | None = None,
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
                resolved_url=resolved_url,
                resolved_redirect_permanent=resolved_redirect_permanent,
                date_refresh_failed=None,
            )
        )


def _assert_stamped_at_write_time(
    value: datetime | None, *, not_before: datetime
) -> None:
    """Assert a refresh outcome timestamp was taken when it was written.

    The refresh job dates each row's outcome from the clock at write time
    rather than from the reference time the run was called with, so the
    assertion is a window: strictly after the reference the test took before
    the run, and no later than the moment it is checked.

    The lower bound is strict on purpose. Stamping from the batch reference
    is exactly the regression this helper stands in for, and the reference a
    test passes to `refresh_inventories` is the same value it passes here, so
    an inclusive bound would admit the regression verbatim and every test
    routed through this helper would keep passing. Strictness costs nothing:
    ``not_before`` is read before the run and the write happens a database
    round-trip later, and the tests truncate it to whole seconds besides,
    which only widens the gap.
    """
    assert value is not None
    assert not_before < value <= datetime.now(tz=UTC)


def test_write_time_assertion_rejects_the_batch_reference() -> None:
    """`_assert_stamped_at_write_time` rejects the run's reference time.

    The helper is the only thing standing between the refresh path and a
    silent return to stamping outcomes at batch start, so its own lower bound
    is worth pinning: a timestamp equal to the reference must fail.
    """
    reference = datetime.now(tz=UTC).replace(microsecond=0)
    with pytest.raises(AssertionError):
        _assert_stamped_at_write_time(reference, not_before=reference)


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

    # refresh_inventories owns its own commits, so it is called without a
    # surrounding transaction.
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
    _assert_stamped_at_write_time(stored.date_fetched, not_before=now)
    assert stored.last_fetch_status is InventoryFetchStatus.success


@pytest.mark.asyncio
async def test_refresh_304_records_chain_from_this_revalidation(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A 304 records the chain walked during that revalidation.

    The 304 says the *content* is unchanged, which says nothing about the
    chain: the row was cached from a temporary chain and this revalidation
    reached the same terminal through a permanent one. The terminal is
    unmoved, so the 304 is trusted and only the chain's permanence is
    rewritten.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    terminal = "https://docs.example.com/en/21/objects.inv"
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
        resolved_url=terminal,
        resolved_redirect_permanent=False,
    )
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(301, headers={"Location": terminal})
    )
    respx_mock.get(terminal).mock(return_value=Response(304))

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    assert summary.revalidated == 1

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.content == INVENTORY_BODY
    assert stored.resolved_url == terminal
    assert stored.resolved_redirect_permanent is True


@pytest.mark.asyncio
async def test_refresh_updates_resolved_url_when_chain_changes(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A chain that moves between fetches rewrites the stored resolved URL.

    The stored value is never carried forward: upstream can re-point the
    alias, and a stale terminal URL would be worse than none.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    old_terminal = "https://docs.example.com/en/20/objects.inv"
    new_terminal = "https://docs.example.com/en/21/objects.inv"
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
        resolved_url=old_terminal,
        resolved_redirect_permanent=True,
    )
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": new_terminal})
    )
    respx_mock.get(new_terminal).mock(
        return_value=Response(
            200, content=b"# Sphinx inventory version 2\nnew"
        )
    )

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    assert summary.refreshed == 1

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.resolved_url == new_terminal
    # The new chain is temporary, so the stored permanence flips too.
    assert stored.resolved_redirect_permanent is False


@pytest.mark.asyncio
async def test_refresh_304_trusted_when_no_terminal_is_stored(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A row with no stored terminal trusts its 304 and records the chain.

    A null ``resolved_url`` means the terminal was never recorded, not that
    the terminal is the requested URL. Every row cached before this branch
    deployed reads that way, and for a redirecting inventory the requested
    URL is exactly what the terminal is *not*, so conflating the two made
    the first post-deploy refresh of every such row discard a perfectly
    good 304 and re-download the whole body.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    terminal = "https://docs.example.com/en/21/objects.inv"
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
        resolved_url=None,
        resolved_redirect_permanent=None,
    )
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": terminal})
    )

    conditional_requests: list[bool] = []

    def respond(request: httpx.Request) -> Response:
        conditional_requests.append("if-none-match" in request.headers)
        return Response(304)

    terminal_route = respx_mock.get(terminal).mock(side_effect=respond)

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    # The terminal is asked once, conditionally, and its 304 is taken at
    # face value: no second chain walk and no body transfer.
    assert terminal_route.call_count == 1
    assert conditional_requests == [True]
    assert summary.revalidated == 1
    assert summary.refreshed == 0
    assert summary.failed == 0

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.content == INVENTORY_BODY
    # The terminal the revalidation walked to is now on the row, so the
    # next refresh has a terminal to hold the origin to.
    assert stored.resolved_url == terminal
    assert stored.resolved_redirect_permanent is False


@pytest.mark.asyncio
async def test_refresh_withholds_validators_from_a_moved_terminal(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A re-pointed chain is never asked to revalidate the copy it lacks.

    The stored validators were minted by the *previous* chain's terminal, so
    a 304 answered by a different terminal would validate a different
    resource than the one the cache holds. Rather than detect that after the
    fact and pay a second chain walk to repair it, the conditional headers
    are simply not sent anywhere but the terminal that minted them: the
    moved terminal answers the only request it gets with the body, and the
    false 304 it was ready to give never has a request to answer.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    old_terminal = "https://docs.example.com/en/21/objects.inv"
    new_terminal = "https://docs.example.com/en/20/objects.inv"
    new_body = b"# Sphinx inventory version 2\nv20 payload"
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
        resolved_url=old_terminal,
        resolved_redirect_permanent=False,
    )
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": new_terminal})
    )

    conditional_requests: list[bool] = []

    def respond(request: httpx.Request) -> Response:
        # The re-pointed terminal answers a false 304 to any conditional
        # request, which is the trap this withholding exists to disarm.
        is_conditional = "if-none-match" in request.headers
        conditional_requests.append(is_conditional)
        if is_conditional:
            return Response(304)
        return Response(200, content=new_body, headers={"ETag": '"v20-etag"'})

    terminal_route = respx_mock.get(new_terminal).mock(side_effect=respond)

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    # One request, carrying no validator, so the moved terminal has to
    # answer with the bytes.
    assert terminal_route.call_count == 1
    assert conditional_requests == [False]
    assert summary.refreshed == 1
    assert summary.revalidated == 0
    assert summary.failed == 0

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.content == new_body
    assert stored.etag == '"v20-etag"'
    assert stored.resolved_url == new_terminal
    _assert_stamped_at_write_time(stored.date_fetched, not_before=now)


@pytest.mark.asyncio
async def test_refresh_with_a_varying_terminal_costs_one_chain_per_run(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """An origin whose terminal never repeats still costs one chain a run.

    A ``Location`` carrying a per-response token or a load-balancer shard
    lands on a different terminal every time, so the stored terminal can
    never match and a detect-then-repair guard would re-walk the chain and
    re-download the body on every run, forever, against a single fetch
    budget. Withholding the validators instead makes that inventory cost
    exactly what an unconditional refresh costs — one chain, one body — and
    no more, however long the terminal keeps moving.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    body = b"# Sphinx inventory version 2\nsharded payload"
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
        resolved_url="https://docs.example.com/shard-0/objects.inv",
        resolved_redirect_permanent=False,
    )

    shards = iter(range(1, 100))

    def redirect(request: httpx.Request) -> Response:
        location = f"https://docs.example.com/shard-{next(shards)}/objects.inv"
        return Response(302, headers={"Location": location})

    terminal_requests: list[tuple[str, bool]] = []

    def respond(request: httpx.Request) -> Response:
        is_conditional = "if-none-match" in request.headers
        terminal_requests.append((str(request.url), is_conditional))
        if is_conditional:
            return Response(304)
        return Response(200, content=body, headers={"ETag": '"shard-etag"'})

    respx_mock.get(INVENTORY_URL).mock(side_effect=redirect)
    respx_mock.get(
        url__regex=r"https://docs\.example\.com/shard-\d+/objects\.inv"
    ).mock(side_effect=respond)

    service = factory.create_intersphinx_cache_service()
    first = await service.refresh_inventories(now=now)
    # Each run dates the row at write time, so the second run needs a
    # reference far enough ahead for the row to be stale again.
    second = await service.refresh_inventories(now=now + timedelta(hours=2))

    assert first.refreshed == 1
    assert second.refreshed == 1
    assert first.failed == 0
    assert second.failed == 0
    # One terminal request per run, each unconditional: no run pays for a
    # second walk of the chain it just walked.
    assert terminal_requests == [
        ("https://docs.example.com/shard-1/objects.inv", False),
        ("https://docs.example.com/shard-2/objects.inv", False),
    ]
    assert respx_mock.calls.call_count == 4


@pytest.mark.asyncio
async def test_refresh_304_without_a_validator_records_a_failure(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A 304 answering a validator-less refresh is upstream misbehavior.

    A negative-cache row carries no content and no validators, so once it
    ages into the due list its refresh goes out unconditional — and a 304
    answering a request that sent nothing to revalidate against is about no
    copy at all. Trusting it would store a content-None/success row, which
    is neither servable nor a live negative-cache entry, and would clobber
    content a concurrent cold miss had just stored.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        content=None,
        etag=None,
        last_modified=None,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
        last_fetch_status=InventoryFetchStatus.failure,
    )
    route = respx_mock.get(INVENTORY_URL).mock(return_value=Response(304))

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    assert route.call_count == 1
    assert summary.failed == 1
    assert summary.revalidated == 0
    assert summary.refreshed == 0

    await _assert_negative_cached(
        factory, error_contains="unconditional", backoff_marked=True
    )


@pytest.mark.asyncio
async def test_refresh_304_from_a_withheld_validator_fails(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A moved terminal that 304s an unconditional request is a failure.

    The stored validators are withheld from a terminal the stored copy was
    not fetched from, so that terminal is asked unconditionally and owes the
    bytes; answering a request that revalidates nothing with a ``304``
    leaves nothing to store. The refresh records a failure and the stored
    copy — content and freshness anchor alike — is left intact for stale
    serving.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    fetched_at = now - timedelta(hours=2)
    old_terminal = "https://docs.example.com/en/21/objects.inv"
    new_terminal = "https://docs.example.com/en/20/objects.inv"
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        date_fetched=fetched_at,
        date_requested=now - timedelta(days=1),
        resolved_url=old_terminal,
        resolved_redirect_permanent=False,
    )
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": new_terminal})
    )
    # The moved terminal answers 304 to everything, conditional or not.
    terminal_route = respx_mock.get(new_terminal).mock(
        return_value=Response(304)
    )

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    assert terminal_route.call_count == 1
    assert summary.failed == 1
    assert summary.revalidated == 0
    assert summary.refreshed == 0

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.content == INVENTORY_BODY
    assert stored.date_fetched == fetched_at
    assert stored.last_fetch_status is InventoryFetchStatus.failure
    assert stored.last_fetch_error is not None
    assert "unconditional" in stored.last_fetch_error


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
    _assert_stamped_at_write_time(stored.date_fetched, not_before=now)


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


@pytest.mark.asyncio
async def test_refresh_bookkeeping_failure_does_not_abort_batch(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database error while *recording* a refresh failure is counted, not
    fatal.

    The guarded backoff write races the very cold miss it defends against, so
    a serialization or deadlock error is the expected contention rather than a
    hypothetical. If it escaped the loop the remaining inventories would never
    be refreshed and the run would end with no summary at all.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    failing_url = "https://failing.example.com/objects.inv"
    ok_url = "https://ok.example.com/objects.inv"
    # The due list is stalest-first, so the broken bookkeeping write lands
    # partway through a batch that still has an inventory left to refresh.
    await _seed_stale_inventory(
        factory,
        failing_url,
        content=b"kept payload",
        date_fetched=now - timedelta(hours=3),
        date_requested=now - timedelta(days=1),
    )
    await _seed_stale_inventory(
        factory,
        ok_url,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )
    respx_mock.get(failing_url).mock(return_value=Response(500))
    respx_mock.get(ok_url).mock(return_value=Response(304))

    unpatched_write = IntersphinxInventoryStore.update_refresh_failure

    async def failing_write(
        self: IntersphinxInventoryStore, url: str, **kwargs: Any
    ) -> bool:
        if url == failing_url:
            raise OperationalError(
                "UPDATE intersphinx_inventory SET last_fetch_status=...",
                {},
                Exception("deadlock detected"),
            )
        return await unpatched_write(self, url, **kwargs)

    monkeypatch.setattr(
        IntersphinxInventoryStore, "update_refresh_failure", failing_write
    )

    with capture_logs() as captured:
        service = factory.create_intersphinx_cache_service()
        summary = await service.refresh_inventories(now=now)

    assert summary.considered == 2
    assert summary.failed == 1
    assert summary.unrecorded_failures == 1
    # The inventory behind the broken write is still refreshed.
    assert summary.revalidated == 1
    assert any(
        event.get("url") == failing_url
        and event.get("event")
        == "Failed to record an intersphinx refresh failure"
        for event in captured
    )
    # The run still reports itself rather than dying inside the loop.
    assert any(
        event.get("event") == "Completed intersphinx inventory refresh"
        for event in captured
    )

    # The row really is unrecorded: no backoff marker, stored copy intact.
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        kept = await store.get_inventory(failing_url)
    assert kept is not None
    assert kept.content == b"kept payload"
    assert kept.date_refresh_failed is None


@pytest.mark.asyncio
async def test_refresh_failure_backs_off_until_the_next_interval(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A failed refresh leaves the front of the due list until the next
    interval.

    Recording nothing would leave the row's stale ``date_fetched`` in place,
    so it would be selected again on the very next run — and, sorting
    stalest-first, ahead of every healthy inventory — for the whole 30-day
    active window, each futile attempt now costing a full redirect chain.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    fetched_at = now - timedelta(hours=2)
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        content=b"kept payload",
        date_fetched=fetched_at,
        date_requested=now - timedelta(days=1),
    )
    route = respx_mock.get(INVENTORY_URL).mock(return_value=Response(500))

    service = factory.create_intersphinx_cache_service()
    first = await service.refresh_inventories(now=now)
    second = await service.refresh_inventories(now=now + timedelta(minutes=5))

    assert first.considered == 1
    assert first.failed == 1
    # The next run does not even consider it, so upstream is not re-tried.
    assert second.considered == 0
    assert route.call_count == 1

    # A fresh, independent session sees the committed failure: it survived
    # the rollback that discards the failed inventory's pending write.
    logger = structlog.get_logger("test")
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    session = await create_async_session(engine)
    store = IntersphinxInventoryStore(session=session, logger=logger)
    stored = await store.get_inventory(INVENTORY_URL)
    await session.close()
    await engine.dispose()

    assert stored is not None
    assert stored.last_fetch_status is InventoryFetchStatus.failure
    assert (
        stored.last_fetch_error
        == "Upstream returned HTTP 500 for the inventory"
    )
    _assert_stamped_at_write_time(stored.date_refresh_failed, not_before=now)
    # The stored copy is intact behind the failure, down to the freshness
    # anchor that dates its content.
    assert stored.content == b"kept payload"
    assert stored.date_fetched == fetched_at


@pytest.mark.asyncio
async def test_refresh_failure_marker_dates_the_attempt_not_the_batch(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """The backoff marker is stamped when the failure is written.

    A refresh batch is serial and each failing inventory can spend the whole
    fetch budget, so a run can be minutes long. Stamping every failure with
    the time the batch *started* would shorten each one's backoff by however
    long the batch had already been running — under the hourly refresh
    CronJob, enough to put a mid-batch failure back in the very next run's
    due list, which is the retry storm the backoff exists to prevent.

    Unlike its neighbours, this test does not truncate ``now`` to whole
    seconds. The whole detection margin here is the ``elapsed`` the fetch
    spends before failing, and truncation would put ``now`` up to a second
    behind the real clock — more slack than that margin, so a batch-start
    regression would clear ``now + elapsed`` on most runs and the test would
    only sometimes fail.
    """
    now = datetime.now(tz=UTC)
    elapsed = timedelta(seconds=0.3)

    async def slow_failure(request: httpx.Request) -> Response:
        # Stand in for a batch that has been running a while by the time this
        # inventory's fetch gives up.
        await asyncio.sleep(elapsed.total_seconds())
        return Response(500)

    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )
    respx_mock.get(INVENTORY_URL).mock(side_effect=slow_failure)

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    assert summary.failed == 1

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.date_refresh_failed is not None
    assert stored.date_refresh_failed >= now + elapsed


@pytest.mark.asyncio
async def test_refresh_304_leaves_a_concurrently_refreshed_row_alone(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A ``304`` write is dropped when the row changed under the batch.

    A row in the due list is stale by construction, so a client cold miss can
    fetch and commit good content while this refresh's own conditional fetch
    is still in flight — up to the whole 30-second budget. The refresh's
    ``304`` validates the copy *it* sent validators for, not the one the cold
    miss stored, so without a guard the write would re-date the newer copy
    from a fetch that never saw it, and (before the write narrowed to the
    columns a revalidation changes) revert its bytes to the due-list snapshot
    and stamp the reverted content fresh for a whole TTL.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    fresh_content = b"# Sphinx inventory version 2\nconcurrent payload"

    async def concurrent_success(request: httpx.Request) -> Response:
        # Stand in for the client cold miss that wins the race: commit good
        # content on an independent session while the refresh fetch is still
        # in flight.
        engine = create_database_engine(
            config.database_url, config.database_password
        )
        session = await create_async_session(engine)
        store = IntersphinxInventoryStore(
            session=session, logger=structlog.get_logger("test")
        )
        async with session.begin():
            await store.upsert_inventory(
                IntersphinxInventory(
                    url=INVENTORY_URL,
                    content=fresh_content,
                    content_type="application/octet-stream",
                    etag='"fresh-etag"',
                    last_modified=None,
                    date_fetched=now,
                    date_requested=now,
                    last_fetch_status=InventoryFetchStatus.success,
                    last_fetch_error=None,
                    date_refresh_failed=None,
                )
            )
        await session.close()
        await engine.dispose()
        return Response(304)

    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        content=b"stale payload",
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )
    respx_mock.get(INVENTORY_URL).mock(side_effect=concurrent_success)

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    # The dropped write is reported rather than counted as a revalidation.
    assert summary.considered == 1
    assert summary.superseded == 1
    assert summary.revalidated == 0
    assert summary.refreshed == 0
    assert summary.failed == 0

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    # The winning cold miss's row stands, down to the freshness anchor that
    # dates its content.
    assert stored.content == fresh_content
    assert stored.etag == '"fresh-etag"'
    assert stored.date_fetched == now


@pytest.mark.asyncio
async def test_refresh_failure_leaves_a_concurrently_refreshed_row_alone(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A failure write is dropped when the row changed under the batch.

    A negative-cache row that ages into the due list is servable again by a
    client cold miss, and that miss can succeed while the refresh job's own
    fetch is still failing. The failure write lands last, so without a guard
    it would stamp a ``failure`` status and a backoff marker onto the fresh
    content the client just stored.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    fresh_content = b"# Sphinx inventory version 2\nconcurrent payload"

    async def concurrent_success(request: httpx.Request) -> Response:
        # Stand in for the client cold miss that wins the race: commit good
        # content on an independent session while the refresh fetch is still
        # in flight.
        engine = create_database_engine(
            config.database_url, config.database_password
        )
        session = await create_async_session(engine)
        store = IntersphinxInventoryStore(
            session=session, logger=structlog.get_logger("test")
        )
        async with session.begin():
            await store.upsert_inventory(
                IntersphinxInventory(
                    url=INVENTORY_URL,
                    content=fresh_content,
                    content_type="application/octet-stream",
                    etag='"fresh-etag"',
                    last_modified=None,
                    date_fetched=now,
                    date_requested=now,
                    last_fetch_status=InventoryFetchStatus.success,
                    last_fetch_error=None,
                    date_refresh_failed=None,
                )
            )
        await session.close()
        await engine.dispose()
        return Response(500)

    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        content=None,
        etag=None,
        last_modified=None,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
        last_fetch_status=InventoryFetchStatus.failure,
    )
    respx_mock.get(INVENTORY_URL).mock(side_effect=concurrent_success)

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    assert summary.failed == 1

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    # The winning cold miss's row stands, cross-column invariants intact.
    assert stored.content == fresh_content
    assert stored.last_fetch_status is InventoryFetchStatus.success
    assert stored.last_fetch_error is None
    assert stored.date_refresh_failed is None


@pytest.mark.asyncio
async def test_refresh_success_after_failure_restores_the_cadence(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A successful refresh clears the backoff a failure left behind."""
    now = datetime.now(tz=UTC).replace(microsecond=0)
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        content=b"kept payload",
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )
    new_body = b"# Sphinx inventory version 2\nrecovered payload"
    respx_mock.get(INVENTORY_URL).mock(
        side_effect=[Response(500), Response(200, content=new_body)]
    )

    service = factory.create_intersphinx_cache_service()
    failed_run = await service.refresh_inventories(now=now)
    # Once the backoff interval has elapsed the inventory is due again. Only
    # the run's cutoffs move forward; the row's own timestamps are still
    # stamped from the real clock at write time.
    recovered_at = now + timedelta(hours=2)
    recovered_run = await service.refresh_inventories(now=recovered_at)

    assert failed_run.failed == 1
    assert recovered_run.refreshed == 1
    assert recovered_run.failed == 0

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.content == new_body
    _assert_stamped_at_write_time(stored.date_fetched, not_before=now)
    assert stored.last_fetch_status is InventoryFetchStatus.success
    assert stored.last_fetch_error is None
    assert stored.date_refresh_failed is None


@pytest.mark.asyncio
async def test_refresh_redirect_hop_dns_failure_skips_one_inventory(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hop that fails to resolve skips its inventory, not the whole batch.

    The failing inventory is the stalest, so it is refreshed first: an
    unconverted ``socket.gaierror`` would escape the per-inventory handler
    and abort the run before the second inventory is ever attempted — and,
    because the aborted row keeps its old ``date_fetched``, it would sort
    first again on every later run and starve the rest of the cache.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    failing_url = "https://failing.example.com/objects.inv"
    ok_url = "https://ok.example.com/objects.inv"
    retired = "https://retired.example.com/objects.inv"
    await _seed_stale_inventory(
        factory,
        failing_url,
        content=b"kept payload",
        date_fetched=now - timedelta(hours=3),
        date_requested=now - timedelta(days=1),
    )
    await _seed_stale_inventory(
        factory,
        ok_url,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )
    respx_mock.get(failing_url).mock(
        return_value=Response(302, headers={"Location": retired})
    )
    ok_route = respx_mock.get(ok_url).mock(return_value=Response(304))

    async def resolve(host: str) -> list[str]:
        if host == "retired.example.com":
            raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure")
        return ["93.184.216.34"]

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    assert summary.failed == 1
    assert summary.revalidated == 1
    # The rest of the batch ran despite the first inventory's failure.
    assert ok_route.call_count == 1

    # The failing inventory keeps its stored content for stale serving.
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        kept = await store.get_inventory(failing_url)
    assert kept is not None
    assert kept.content == b"kept payload"


@pytest.mark.asyncio
async def test_refresh_rebound_stored_url_detail_omits_resolution(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored URL the guard now refuses stores a scrubbed detail.

    The pre-fetch re-guard exists for the DNS-rebinding case, so its
    rejection names the host and what it resolves to *from inside the
    cluster*. That reason is written to ``last_fetch_error``, and a row
    whose content is a negative-cache entry replays that column verbatim in
    the 502 body of every later request — so the row must carry the generic
    detail, not Ook's own DNS view.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        content=b"kept payload",
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )
    route = respx_mock.get(INVENTORY_URL).mock(return_value=Response(304))

    async def resolve(host: str) -> list[str]:
        return ["10.0.0.5"]

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    assert summary.failed == 1
    # A URL the guard refuses is never fetched.
    assert route.call_count == 0

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert (
        stored.last_fetch_error
        == "The cached inventory URL is no longer allowed to be fetched"
    )
    assert "10.0.0.5" not in stored.last_fetch_error
    assert "docs.example.com" not in stored.last_fetch_error


@pytest.mark.asyncio
async def test_refresh_rebound_stored_url_logs_the_specific_reason(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason scrubbed off the row is logged for operators instead.

    One record at the failure boundary has to name both halves: the URL that
    failed and the guard's specific reason, which is all that is left of it
    once the stored detail is generic.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        content=b"kept payload",
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )
    respx_mock.get(INVENTORY_URL).mock(return_value=Response(304))

    async def resolve(host: str) -> list[str]:
        return ["10.0.0.5"]

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    with capture_logs() as logs:
        service = factory.create_intersphinx_cache_service()
        await service.refresh_inventories(now=now)

    rejections = [
        record
        for record in logs
        if record.get("cache_status") == "refresh-failure"
        and record.get("reason")
    ]
    assert len(rejections) == 1
    record = rejections[0]
    assert record["url"] == INVENTORY_URL
    assert "10.0.0.5" in record["reason"]
    assert record["log_level"] == "warning"


@pytest.mark.asyncio
async def test_refresh_guard_resolution_bounded_by_the_time_budget(
    factory: Factory,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung resolver on the stored URL skips one inventory, not the batch.

    The refresh path re-guards the stored URL before fetching it, and that
    lookup runs serially ahead of every other inventory in the run: without
    the budget covering it, one host whose DNS ladder hangs stalls the whole
    batch rather than costing its own inventory a skip.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )
    route = respx_mock.get(INVENTORY_URL).mock(return_value=Response(304))

    async def resolve(host: str) -> list[str]:
        await asyncio.sleep(3)
        return ["93.184.216.34"]

    monkeypatch.setattr(intersphinx_service, "_default_resolve_host", resolve)

    service = _make_service(factory, request_timeout=timedelta(seconds=0.2))
    start = time.monotonic()
    summary = await service.refresh_inventories(now=now)
    assert time.monotonic() - start < 1.5

    assert summary.failed == 1
    assert summary.revalidated == 0
    # The URL whose guard never finished is never fetched.
    assert route.call_count == 0

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    # The stored copy keeps serving stale; only the failure columns move.
    assert stored.content == INVENTORY_BODY
    assert stored.last_fetch_error is not None
    assert "time budget" in stored.last_fetch_error


@pytest.mark.asyncio
async def test_refresh_malformed_redirect_location_counts_as_failure(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A malformed ``Location`` on the refresh path fails just that
    inventory.

    Its stored copy is left untouched so it keeps serving stale, exactly as
    for any other upstream misbehavior.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        content=b"kept payload",
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            301, headers={"Location": "https://docs.example.com:notaport/x"}
        )
    )

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    assert summary.failed == 1
    assert summary.refreshed == 0
    assert summary.revalidated == 0

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        kept = await store.get_inventory(INVENTORY_URL)
    assert kept is not None
    assert kept.content == b"kept payload"


@pytest.mark.asyncio
async def test_refresh_overlong_redirect_location_skips_one_inventory(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A ``Location`` httpx cannot join skips its inventory, not the batch.

    httpx joins a 3xx ``Location`` itself even under
    ``follow_redirects=False``, and ``httpx.InvalidURL`` is not an
    ``httpx.HTTPError``, so an unconverted one escapes the per-inventory
    handler. The failing inventory is the stalest, so it is refreshed
    first: the escape would abort the run before the second inventory is
    ever attempted and — because the aborted row keeps its old
    ``date_fetched`` — it would sort first again on every later run,
    letting one hostile origin starve the rest of the cache.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    failing_url = "https://failing.example.com/objects.inv"
    ok_url = "https://ok.example.com/objects.inv"
    await _seed_stale_inventory(
        factory,
        failing_url,
        content=b"kept payload",
        date_fetched=now - timedelta(hours=3),
        date_requested=now - timedelta(days=1),
    )
    await _seed_stale_inventory(
        factory,
        ok_url,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )
    respx_mock.get(failing_url).mock(
        return_value=Response(302, headers={"Location": "/" + "a" * 65530})
    )
    ok_route = respx_mock.get(ok_url).mock(return_value=Response(304))

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    assert summary.failed == 1
    assert summary.revalidated == 1
    # The rest of the batch ran despite the first inventory's failure.
    assert ok_route.call_count == 1

    # The failing inventory keeps its stored content for stale serving,
    # and records why it failed.
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        kept = await store.get_inventory(failing_url)
    assert kept is not None
    assert kept.content == b"kept payload"
    assert kept.last_fetch_error is not None
    assert "malformed URL" in kept.last_fetch_error


@pytest.mark.asyncio
async def test_refresh_oversized_response_counts_as_failure(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A refresh 200 over the size cap is a failure and keeps stored content.

    The oversized response is abandoned and counted as a per-inventory
    failure; the stored copy is left untouched so it keeps serving stale.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        content=b"kept payload",
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=b"x" * 200)  # over the 64-byte cap
    )

    service = _make_service(factory, max_content_size=64)
    summary = await service.refresh_inventories(now=now)

    assert summary.failed == 1
    assert summary.refreshed == 0
    assert summary.revalidated == 0

    # The stored copy is untouched, so it keeps serving stale.
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        kept = await store.get_inventory(INVENTORY_URL)
    assert kept is not None
    assert kept.content == b"kept payload"


@pytest.mark.asyncio
async def test_refresh_empty_response_counts_as_failure(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A refresh ``200`` carrying no bytes fails and keeps stored content.

    An empty body is no more servable on this path than on the cold-miss
    one, and here it would additionally overwrite a good stored copy with
    nothing, so it must be a per-inventory failure that leaves the stored
    copy serving stale.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        content=b"kept payload",
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )
    respx_mock.get(INVENTORY_URL).mock(return_value=Response(200, content=b""))

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    assert summary.failed == 1
    assert summary.refreshed == 0
    assert summary.revalidated == 0

    # The stored copy is untouched, so it keeps serving stale.
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        kept = await store.get_inventory(INVENTORY_URL)
    assert kept is not None
    assert kept.content == b"kept payload"


@pytest.mark.asyncio
async def test_refresh_commits_each_inventory_independently(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """Each inventory's refresh outcome is committed on its own, without a
    caller-managed transaction.

    A separate database connection sees both outcomes, proving the batch
    committed each as it went rather than leaving them pending in one caller
    transaction that a mid-run crash would discard wholesale.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    url_a = "https://a.example.com/objects.inv"
    url_b = "https://b.example.com/objects.inv"
    await _seed_stale_inventory(
        factory,
        url_a,
        content=b"old-a payload",
        etag='"etag-a"',
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
    )
    await _seed_stale_inventory(
        factory,
        url_b,
        date_fetched=now - timedelta(hours=3),
        date_requested=now - timedelta(days=1),
    )
    new_a = b"# Sphinx inventory version 2\nnew-a payload"
    respx_mock.get(url_a).mock(
        return_value=Response(200, content=new_a, headers={"ETag": '"new-a"'})
    )
    respx_mock.get(url_b).mock(return_value=Response(304))

    # No surrounding transaction: the service owns its own commit boundaries.
    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    assert summary.considered == 2
    assert summary.refreshed == 1
    assert summary.revalidated == 1

    # A fresh, independent session sees both committed outcomes.
    logger = structlog.get_logger("test")
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    session = await create_async_session(engine)
    store = IntersphinxInventoryStore(session=session, logger=logger)
    stored_a = await store.get_inventory(url_a)
    stored_b = await store.get_inventory(url_b)
    await session.close()
    await engine.dispose()

    assert stored_a is not None
    assert stored_a.content == new_a
    assert stored_b is not None
    _assert_stamped_at_write_time(stored_b.date_fetched, not_before=now)


@pytest.mark.asyncio
async def test_refresh_sends_validators_only_at_the_stored_terminal(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A recorded terminal gets both validators and the hops before it none.

    The stored validators are an assertion about the terminal that minted
    them, so a row that recorded its terminal has them sent there and
    nowhere else: the redirecting hop on the way carries none, and the
    modification date survives the redirect because the request it rides is
    aimed at exactly the URL it was read from.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    terminal = "https://docs.example.com/en/21/objects.inv"
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
        resolved_url=terminal,
        resolved_redirect_permanent=True,
    )

    seen: list[dict[str, str]] = []

    def record(request: httpx.Request) -> Response:
        seen.append(dict(request.headers))
        if str(request.url) == INVENTORY_URL:
            return Response(301, headers={"Location": terminal})
        return Response(304)

    respx_mock.get(INVENTORY_URL).mock(side_effect=record)
    terminal_route = respx_mock.get(terminal).mock(side_effect=record)

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    assert terminal_route.call_count == 1
    assert summary.revalidated == 1
    assert summary.failed == 0
    assert len(seen) == 2
    # The requested URL did not mint these validators, so it is asked
    # unconditionally even though it is only going to redirect.
    assert "if-none-match" not in seen[0]
    assert "if-modified-since" not in seen[0]
    # The terminal did mint them, so both hold there.
    assert seen[1].get("if-none-match") == '"stored-etag"'
    assert seen[1].get("if-modified-since") == "Wed, 01 Jan 2025 00:00:00 GMT"

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.content == INVENTORY_BODY
    _assert_stamped_at_write_time(stored.date_fetched, not_before=now)


@pytest.mark.asyncio
async def test_refresh_drops_if_modified_since_across_a_redirect(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """With no recorded terminal, a chain keeps the ETag but drops the date.

    A row whose terminal was never recorded has no URL to hold its
    validators to, so they ride the whole chain. A strong validator stays
    trustworthy wherever that chain lands (RFC 9110 §13.1.3 puts
    ``If-None-Match`` ahead of ``If-Modified-Since`` anyway), but a stored
    modification date compared against a terminal the chain has since been
    re-pointed at is what lets an *older* resource answer a false 304 — so
    the date is sent only while the request is still aimed at the URL it was
    read from, and a ``304`` at the end still revalidates the stored copy in
    place rather than re-downloading it.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    terminal = "https://docs.example.com/en/21/objects.inv"
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
        resolved_url=None,
        resolved_redirect_permanent=None,
    )

    seen: list[dict[str, str]] = []

    def record(request: httpx.Request) -> Response:
        seen.append(dict(request.headers))
        if str(request.url) == INVENTORY_URL:
            return Response(302, headers={"Location": terminal})
        return Response(304)

    respx_mock.get(INVENTORY_URL).mock(side_effect=record)
    terminal_route = respx_mock.get(terminal).mock(side_effect=record)

    service = factory.create_intersphinx_cache_service()
    summary = await service.refresh_inventories(now=now)

    assert terminal_route.call_count == 1
    assert summary.revalidated == 1
    assert summary.failed == 0
    assert len(seen) == 2
    # The requested URL is the last hop known to be one the stored
    # validators could have been minted by, so it carries both.
    assert seen[0].get("if-none-match") == '"stored-etag"'
    assert seen[0].get("if-modified-since") == "Wed, 01 Jan 2025 00:00:00 GMT"
    # Past the redirect, only the strong validator survives.
    assert seen[1].get("if-none-match") == '"stored-etag"'
    assert "if-modified-since" not in seen[1]

    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.content == INVENTORY_BODY
    _assert_stamped_at_write_time(stored.date_fetched, not_before=now)


@pytest.mark.asyncio
async def test_cold_miss_logs_terminal_url_and_hop_count(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A cold-miss fetch logs the terminal URL and the hop count."""
    terminal = "https://docs.example.com/en/21/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(301, headers={"Location": terminal})
    )
    respx_mock.get(terminal).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    with structlog.testing.capture_logs() as captured:
        async with factory.db_session.begin():
            service = factory.create_intersphinx_cache_service()
            await service.get_inventory(INVENTORY_URL)

    assert any(
        event.get("url") == INVENTORY_URL
        and event.get("final_url") == terminal
        and event.get("redirect_hops") == 1
        for event in captured
    )


@pytest.mark.asyncio
async def test_refresh_logs_terminal_url_and_hop_count(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A refresh logs the terminal URL and the hop count."""
    now = datetime.now(tz=UTC).replace(microsecond=0)
    terminal = "https://docs.example.com/en/21/objects.inv"
    await _seed_stale_inventory(
        factory,
        INVENTORY_URL,
        date_fetched=now - timedelta(hours=2),
        date_requested=now - timedelta(days=1),
        resolved_url=terminal,
        resolved_redirect_permanent=True,
    )
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(301, headers={"Location": terminal})
    )
    respx_mock.get(terminal).mock(return_value=Response(304))

    service = factory.create_intersphinx_cache_service()
    with structlog.testing.capture_logs() as captured:
        await service.refresh_inventories(now=now)

    assert any(
        event.get("cache_status") == "revalidated"
        and event.get("final_url") == terminal
        and event.get("redirect_hops") == 1
        for event in captured
    )
