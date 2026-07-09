"""Tests for the /ook/intersphinx endpoints."""

from __future__ import annotations

import pytest
import respx
import structlog
from httpx import AsyncClient, Response
from safir.database import create_async_session, create_database_engine

from ook.config import config
from ook.storage.intersphinxstore import IntersphinxInventoryStore

INVENTORY_URL = "https://docs.example.com/en/latest/objects.inv"
"""An origin ``objects.inv`` URL used across the endpoint tests."""

INVENTORY_BODY = b"# Sphinx inventory version 2\nfake objects.inv payload"
"""A stand-in for the binary ``objects.inv`` payload."""


@pytest.mark.asyncio
async def test_cold_miss_serves_inventory(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A cold-miss GET fetches, stores, and serves the origin bytes."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    response = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
    )

    assert response.status_code == 200
    assert response.content == INVENTORY_BODY
    assert response.headers["content-type"] == "application/octet-stream"
    # The Age header reports seconds since the inventory was fetched.
    assert "age" in response.headers
    assert int(response.headers["age"]) >= 0
    assert respx_mock.calls.call_count == 1

    # The fetched inventory is persisted with its last-requested time set.
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
    assert stored.content == INVENTORY_BODY
    assert stored.date_requested is not None
