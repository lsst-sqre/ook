"""Tests for the IntersphinxCacheService."""

from __future__ import annotations

import pytest
import respx
import structlog
from httpx import Response

from ook.domain.intersphinx import InventoryFetchStatus
from ook.factory import Factory

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
