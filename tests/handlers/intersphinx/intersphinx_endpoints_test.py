"""Tests for the /ook/intersphinx endpoints."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime

import pytest
import respx
import structlog
from httpx import AsyncClient, Response
from safir.database import create_async_session, create_database_engine

from ook.config import config
from ook.domain.intersphinx import InventoryFetchStatus
from ook.storage.intersphinxstore import IntersphinxInventoryStore

INVENTORY_URL = "https://docs.example.com/en/latest/objects.inv"
"""An origin ``objects.inv`` URL used across the endpoint tests."""

INVENTORY_BODY = b"# Sphinx inventory version 2\nfake objects.inv payload"
"""A stand-in for the binary ``objects.inv`` payload."""


def _expected_etag(content: bytes) -> str:
    """Return the strong ETag the endpoint emits: quoted SHA-256 hex."""
    return f'"{hashlib.sha256(content).hexdigest()}"'


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


@pytest.mark.asyncio
async def test_warm_hit_serves_from_cache_with_age(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A warm-hit GET serves the cached bytes with an Age header and no
    second upstream request.
    """
    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    # Prime the cache with a cold miss.
    first = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
    )
    assert first.status_code == 200
    assert route.call_count == 1

    # The warm hit is served from Postgres without contacting upstream.
    second = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
    )
    assert second.status_code == 200
    assert second.content == INVENTORY_BODY
    assert "age" in second.headers
    assert int(second.headers["age"]) >= 0
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_http_url_rejected_with_400(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A non-HTTPS URL is rejected with a 400 and never fetched."""
    http_url = "http://docs.example.com/en/latest/objects.inv"
    route = respx_mock.get(http_url).mock(
        return_value=Response(200, content=INVENTORY_BODY)
    )

    response = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": http_url},
    )

    assert response.status_code == 400
    # The guarded URL is never fetched from upstream.
    assert route.call_count == 0


@pytest.mark.asyncio
async def test_cold_miss_upstream_failure_returns_502(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A cold-miss upstream failure returns 502 and is negatively cached.

    The 502 carries a detail message, and a repeat request within the
    negative TTL is served from the negative cache without a second
    upstream call.
    """
    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(500, content=b"boom")
    )

    first = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
    )
    assert first.status_code == 502
    assert first.json()["detail"]
    assert route.call_count == 1

    # The negatively-cached failure is committed, so a repeat request is
    # served from the cache without re-contacting upstream.
    second = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
    )
    assert second.status_code == 502
    assert route.call_count == 1

    # The stored row is a failure-status/no-content negative-cache entry.
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
    assert stored.content is None
    assert stored.last_fetch_status is InventoryFetchStatus.failure


@pytest.mark.asyncio
async def test_cold_miss_emits_etag(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A cold-miss 200 carries a strong ETag of the served bytes."""
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
    assert response.headers["etag"] == _expected_etag(INVENTORY_BODY)
    # The Age header and content-type behavior are unchanged.
    assert "age" in response.headers
    assert response.headers["content-type"] == "application/octet-stream"


@pytest.mark.asyncio
async def test_warm_hit_emits_etag(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A warm-hit 200 carries the same strong ETag as the cold miss."""
    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    first = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
    )
    assert first.status_code == 200
    assert route.call_count == 1

    second = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
    )
    assert second.status_code == 200
    # No second upstream fetch, and the warm-hit ETag matches the cold-miss
    # ETag for the same cached bytes.
    assert route.call_count == 1
    assert second.headers["etag"] == _expected_etag(INVENTORY_BODY)
    assert second.headers["etag"] == first.headers["etag"]


@pytest.mark.asyncio
async def test_etag_stable_across_repeated_requests(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """Repeated requests for unchanged cached bytes return the same ETag."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    etags = set()
    for _ in range(3):
        response = await client.get(
            f"{config.path_prefix}/intersphinx/inventory",
            params={"url": INVENTORY_URL},
        )
        assert response.status_code == 200
        etags.add(response.headers["etag"])

    assert etags == {_expected_etag(INVENTORY_BODY)}


@pytest.mark.asyncio
async def test_etag_changes_when_cached_bytes_change(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """The ETag changes when the cached inventory bytes change."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    first = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
    )
    assert first.status_code == 200
    assert first.headers["etag"] == _expected_etag(INVENTORY_BODY)

    # Overwrite the cached bytes in place so a subsequent warm hit serves a
    # different representation.
    new_body = INVENTORY_BODY + b" (revised)"
    logger = structlog.get_logger("test")
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    session = await create_async_session(engine)
    store = IntersphinxInventoryStore(session=session, logger=logger)
    stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    await store.upsert_inventory(
        replace(stored, content=new_body, date_fetched=datetime.now(tz=UTC))
    )
    await session.commit()
    await session.close()
    await engine.dispose()

    second = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
    )
    assert second.status_code == 200
    assert second.content == new_body
    assert second.headers["etag"] == _expected_etag(new_body)
    assert second.headers["etag"] != first.headers["etag"]


async def _prime_cache(client: AsyncClient) -> str:
    """Prime the inventory cache with a cold miss and return its ETag."""
    response = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
    )
    assert response.status_code == 200
    return response.headers["etag"]


@pytest.mark.asyncio
async def test_if_none_match_exact_returns_304(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """If-None-Match equal to the current ETag returns 304 with no body."""
    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    etag = await _prime_cache(client)
    assert route.call_count == 1

    response = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
        headers={"If-None-Match": etag},
    )

    assert response.status_code == 304
    # The 304 carries the same ETag but no body and no Age header.
    assert response.headers["etag"] == etag
    assert response.content == b""
    assert "age" not in response.headers
    # No second upstream fetch was needed to revalidate.
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_if_none_match_mismatch_returns_200(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A stale/other validator returns 200 with the full body and ETag."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    await _prime_cache(client)

    response = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
        headers={"If-None-Match": '"stale-validator"'},
    )

    assert response.status_code == 200
    assert response.content == INVENTORY_BODY
    assert response.headers["etag"] == _expected_etag(INVENTORY_BODY)
    assert "age" in response.headers


@pytest.mark.asyncio
async def test_if_none_match_star_returns_304(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """If-None-Match: * returns 304 when a cached inventory exists."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    etag = await _prime_cache(client)

    response = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
        headers={"If-None-Match": "*"},
    )

    assert response.status_code == 304
    assert response.headers["etag"] == etag
    assert response.content == b""


@pytest.mark.asyncio
async def test_if_none_match_weak_prefix_returns_304(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A W/-prefixed validator matching the opaque tag returns 304."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    etag = await _prime_cache(client)

    response = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
        headers={"If-None-Match": f"W/{etag}"},
    )

    assert response.status_code == 304
    assert response.headers["etag"] == etag
    assert response.content == b""


@pytest.mark.asyncio
async def test_if_none_match_list_returns_304(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A comma-separated list containing the current ETag returns 304."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    etag = await _prime_cache(client)

    response = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
        headers={"If-None-Match": f'"other-validator", {etag}'},
    )

    assert response.status_code == 304
    assert response.headers["etag"] == etag
    assert response.content == b""


@pytest.mark.asyncio
async def test_if_none_match_stale_after_change_returns_200(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """After the cached bytes change, the old validator returns 200."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    old_etag = await _prime_cache(client)

    # Overwrite the cached bytes so the stored representation changes.
    new_body = INVENTORY_BODY + b" (revised)"
    logger = structlog.get_logger("test")
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    session = await create_async_session(engine)
    store = IntersphinxInventoryStore(session=session, logger=logger)
    stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    await store.upsert_inventory(
        replace(stored, content=new_body, date_fetched=datetime.now(tz=UTC))
    )
    await session.commit()
    await session.close()
    await engine.dispose()

    response = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
        headers={"If-None-Match": old_etag},
    )

    assert response.status_code == 200
    assert response.content == new_body
    assert response.headers["etag"] == _expected_etag(new_body)
    assert response.headers["etag"] != old_etag
