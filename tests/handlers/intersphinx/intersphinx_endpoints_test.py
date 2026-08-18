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
from ook.handlers.intersphinx.endpoints import (
    MAX_PERMANENT_REDIRECT_URL_LENGTH,
)
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


@pytest.mark.parametrize(
    "malformed_url",
    [
        # A port ``urlsplit`` never validates, so the guard's own parse
        # accepts it and only httpx refuses it.
        "https://docs.example.com:notaport/objects.inv",
        # An unterminated IPv6 literal, which ``urlsplit`` itself refuses.
        "https://[::1/objects.inv",
    ],
)
@pytest.mark.asyncio
async def test_unparseable_url_rejected_with_400(
    client: AsyncClient,
    respx_mock: respx.Router,
    malformed_url: str,
) -> None:
    """A URL no parser accepts is a 400, not an unhandled 500.

    Both shapes name a plausible public host, so nothing but the parse
    check stands between them and an upstream fetch.
    """
    response = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": malformed_url},
    )

    assert response.status_code == 400
    assert "could not be parsed" in response.json()["detail"][0]["msg"]
    # Nothing is fetched from upstream for a URL that never parsed.
    assert respx_mock.calls.call_count == 0


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


PERMANENT_HOP = "https://docs.example.com/en/stable/objects.inv"
"""The first hop of the all-permanent redirect chain used in these tests."""

PERMANENT_TERMINAL = "https://example.com/docs/stable/objects.inv"
"""The terminal URL of the all-permanent redirect chain."""


def _mock_permanent_chain(respx_mock: respx.Router) -> respx.Route:
    """Mock an all-permanent (301 then 308) chain to a served inventory.

    Returns the route for the originally-requested URL so a test can assert
    the origin is contacted exactly once.
    """
    route = respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(301, headers={"Location": PERMANENT_HOP})
    )
    respx_mock.get(PERMANENT_HOP).mock(
        return_value=Response(308, headers={"Location": PERMANENT_TERMINAL})
    )
    respx_mock.get(PERMANENT_TERMINAL).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )
    return route


@pytest.mark.asyncio
async def test_permanent_redirect_header_on_200(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A 200 for an all-permanent chain names the resolved URL."""
    _mock_permanent_chain(respx_mock)

    response = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
    )

    assert response.status_code == 200
    assert response.content == INVENTORY_BODY
    assert (
        response.headers["x-ook-inventory-permanent-redirect"]
        == PERMANENT_TERMINAL
    )


@pytest.mark.asyncio
async def test_permanent_redirect_header_on_304(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A 304 still names the resolved URL, from the cached row alone.

    A client that holds the current bytes only ever revalidates, so without
    this it would never learn its configured URL has permanently moved.
    """
    route = _mock_permanent_chain(respx_mock)

    etag = await _prime_cache(client)
    assert route.call_count == 1

    response = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
        headers={"If-None-Match": etag},
    )

    assert response.status_code == 304
    assert response.content == b""
    assert (
        response.headers["x-ook-inventory-permanent-redirect"]
        == PERMANENT_TERMINAL
    )
    # The header is served from the cached row, with no upstream contact.
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_permanent_redirect_header_omits_location_fragment(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A fragment on the final ``Location`` is not served in the header.

    The header value is pasted into ``intersphinx_mapping`` by a doc
    author, so it has to name the inventory itself; a fragment is display
    metadata for a document and names nothing there.
    """
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            301, headers={"Location": f"{PERMANENT_TERMINAL}#moved"}
        )
    )
    respx_mock.get(PERMANENT_TERMINAL).mock(
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
    assert (
        response.headers["x-ook-inventory-permanent-redirect"]
        == PERMANENT_TERMINAL
    )


@pytest.mark.asyncio
async def test_no_permanent_redirect_header_for_temporary_chain(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """An all-temporary chain gets no header, on a 200 or a 304.

    This is the SQLAlchemy shape: an all-302 chain whose ``latest`` alias
    legitimately moves, so the requested URL is still the right one to ask
    for and there is nothing for a doc author to fix. The mixed
    permanent-then-temporary shape is covered by ``is_permanent_chain``'s
    own tests.
    """
    temporary_hop = "https://docs.example.com/en/rolling/objects.inv"
    temporary_terminal = "https://docs.example.com/en/21/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(302, headers={"Location": temporary_hop})
    )
    respx_mock.get(temporary_hop).mock(
        return_value=Response(302, headers={"Location": temporary_terminal})
    )
    respx_mock.get(temporary_terminal).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    etag = await _prime_cache(client)

    ok = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
    )
    assert ok.status_code == 200
    assert "x-ook-inventory-permanent-redirect" not in ok.headers

    not_modified = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
        headers={"If-None-Match": etag},
    )
    assert not_modified.status_code == 304
    assert "x-ook-inventory-permanent-redirect" not in not_modified.headers


@pytest.mark.asyncio
async def test_no_permanent_redirect_header_without_redirect(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A URL that does not redirect gets no header, on a 200 or a 304."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    etag = await _prime_cache(client)

    ok = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
    )
    assert ok.status_code == 200
    assert "x-ook-inventory-permanent-redirect" not in ok.headers

    not_modified = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
        headers={"If-None-Match": etag},
    )
    assert not_modified.status_code == 304
    assert "x-ook-inventory-permanent-redirect" not in not_modified.headers


def _overlong_terminal(length: int) -> str:
    """Return a permanent-chain terminal URL of exactly ``length`` chars."""
    prefix = "https://example.com/"
    suffix = "/objects.inv"
    filler = length - len(prefix) - len(suffix)
    assert filler > 0
    return f"{prefix}{'a' * filler}{suffix}"


@pytest.mark.asyncio
async def test_no_permanent_redirect_header_for_overlong_url(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """An over-long resolved URL is omitted rather than sent or truncated.

    The value is upstream-controlled and stored unbounded, but ingress
    header buffers are only a few kilobytes; emitting a multi-kilobyte
    header would turn every cache hit for the row into an ingress-level
    502 that Ook itself logs as a successful serve.
    """
    terminal = _overlong_terminal(MAX_PERMANENT_REDIRECT_URL_LENGTH + 1)
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(301, headers={"Location": terminal})
    )
    respx_mock.get(terminal).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    etag = await _prime_cache(client)

    ok = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
    )
    assert ok.status_code == 200
    assert ok.content == INVENTORY_BODY
    assert "x-ook-inventory-permanent-redirect" not in ok.headers

    not_modified = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
        headers={"If-None-Match": etag},
    )
    assert not_modified.status_code == 304
    assert "x-ook-inventory-permanent-redirect" not in not_modified.headers


@pytest.mark.asyncio
async def test_permanent_redirect_header_at_length_bound(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A resolved URL exactly at the bound is still emitted."""
    terminal = _overlong_terminal(MAX_PERMANENT_REDIRECT_URL_LENGTH)
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(301, headers={"Location": terminal})
    )
    respx_mock.get(terminal).mock(
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
    assert response.headers["x-ook-inventory-permanent-redirect"] == terminal


@pytest.mark.asyncio
async def test_permanent_redirect_header_documented_on_both_responses(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """The 200 and 304 responses document the header the endpoint emits.

    The documented name and schema are pinned to literals and the name is
    looked up in a live response, rather than compared with
    ``PERMANENT_REDIRECT_HEADER_SPEC``: the published OpenAPI is generated
    *from* that object, so comparing the two cannot fail, and renaming the
    header or retyping its schema would silently move the documented
    contract away from the emitted one. Documenteer reads the published
    document, so the two drifting apart is the failure this test exists to
    catch.
    """
    terminal = "https://docs.example.com/en/21/objects.inv"
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(301, headers={"Location": terminal})
    )
    respx_mock.get(terminal).mock(
        return_value=Response(
            200,
            content=INVENTORY_BODY,
            headers={"Content-Type": "application/octet-stream"},
        )
    )
    served = await client.get(
        f"{config.path_prefix}/intersphinx/inventory",
        params={"url": INVENTORY_URL},
    )
    assert served.status_code == 200

    response = await client.get(f"{config.path_prefix}/openapi.json")
    assert response.status_code == 200
    operation = response.json()["paths"][
        f"{config.path_prefix}/intersphinx/inventory"
    ]["get"]

    documented = operation["responses"]["200"]["headers"]
    assert set(documented) == {"X-Ook-Inventory-Permanent-Redirect"}
    assert documented["X-Ook-Inventory-Permanent-Redirect"]["schema"] == {
        "type": "string",
        "format": "uri",
    }
    # The documented name is the one a live response actually carries.
    (documented_name,) = documented
    assert served.headers[documented_name] == terminal
    # Both responses document it identically, from the one shared block.
    assert operation["responses"]["304"]["headers"] == documented


@pytest.mark.asyncio
async def test_openapi_documents_304_cache_retention_caveat(
    client: AsyncClient,
) -> None:
    """The endpoint description warns that a 304 cannot withdraw the header.

    RFC 9111 §4.3.4 cache-update rules let a 304 update the headers it
    carries but never delete the ones it omits, so a client behind a
    spec-compliant HTTP cache could otherwise learn the flag and never
    unlearn it.
    """
    response = await client.get(f"{config.path_prefix}/openapi.json")
    assert response.status_code == 200
    description = response.json()["paths"][
        f"{config.path_prefix}/intersphinx/inventory"
    ]["get"]["description"]

    assert "RFC 9111" in description


@pytest.mark.asyncio
async def test_openapi_documents_header_last_successful_fetch_semantics(
    client: AsyncClient,
) -> None:
    """The description dates the header to the last successful fetch.

    A row whose refreshes keep failing keeps its resolved-redirect columns
    — deliberately, so one transient failure does not withdraw the signal
    for a whole TTL — which means the header can outlive the chain it was
    observed on. That staleness is documented rather than suppressed, and
    ``Age`` is what tells a client how old the observation is.
    """
    response = await client.get(f"{config.path_prefix}/openapi.json")
    assert response.status_code == 200
    description = response.json()["paths"][
        f"{config.path_prefix}/intersphinx/inventory"
    ]["get"]["description"]

    assert "last successful fetch" in description
    assert "``Age``" in description


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
