"""Tests for the POST /ook/ingest/intersphinx endpoint."""

from __future__ import annotations

import pytest
import respx
import sphobjinv
from httpx import AsyncClient, Response

from ook.config import config

INGEST_URL = f"{config.path_prefix}/ingest/intersphinx"
"""The manual ingest trigger."""

SOURCES_URL = f"{config.path_prefix}/intersphinx/sources"
"""The registry the trigger ingests from."""

INVENTORY_URL = "https://a.example/en/latest/objects.inv"
"""The first registered site's inventory URL."""

OTHER_INVENTORY_URL = "https://b.example/objects.inv"
"""The second registered site's inventory URL."""


def _inventory(name: str) -> bytes:
    """Build a one-object ``objects.inv`` payload documenting *name*."""
    inventory = sphobjinv.Inventory()
    inventory.project = "Example"
    inventory.version = "1.0"
    inventory.objects.append(
        sphobjinv.DataObjStr(
            name=name,
            domain="py",
            role="class",
            priority="1",
            uri=f"api.html#{name}",
            dispname="-",
        )
    )
    return sphobjinv.compress(inventory.data_file())


async def _register(client: AsyncClient, *, url: str, title: str) -> str:
    """Register a documentation source through the registry API.

    Returns
    -------
    str
        The registration's published Crockford Base32 ID.
    """
    response = await client.post(
        SOURCES_URL, json={"url": url, "title": title}
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.asyncio
async def test_post_ingest_intersphinx_ingests_every_source(
    client: AsyncClient, respx_mock: respx.Router
) -> None:
    """A trigger with no source URL ingests the whole registry."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=_inventory("pkg.Thing"))
    )
    respx_mock.get(OTHER_INVENTORY_URL).mock(
        return_value=Response(200, content=_inventory("other.Thing"))
    )
    await _register(client, url=INVENTORY_URL, title="A docs")
    await _register(client, url=OTHER_INVENTORY_URL, title="B docs")

    response = await client.post(INGEST_URL)

    assert response.status_code == 200
    data = response.json()
    assert data["success_count"] == 2
    assert data["failure_count"] == 0
    assert {source["url"] for source in data["sources"]} == {
        INVENTORY_URL,
        OTHER_INVENTORY_URL,
    }

    # The run's outcome is visible on the registrations it stamped.
    listing = await client.get(SOURCES_URL)
    assert [source["last_status"] for source in listing.json()] == [
        "success",
        "success",
    ]


@pytest.mark.asyncio
async def test_post_ingest_intersphinx_reports_the_registration_id(
    client: AsyncClient, respx_mock: respx.Router
) -> None:
    """A result names its source by the Base32 ID the registry published,
    so an outcome can be carried straight back to the registration.
    """
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=_inventory("pkg.Thing"))
    )
    source_id = await _register(client, url=INVENTORY_URL, title="A docs")

    response = await client.post(INGEST_URL)

    assert response.status_code == 200
    assert [source["source_id"] for source in response.json()["sources"]] == [
        source_id
    ]


@pytest.mark.asyncio
async def test_post_ingest_intersphinx_can_name_one_source(
    client: AsyncClient, respx_mock: respx.Router
) -> None:
    """A trigger naming a source URL ingests that source alone."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=_inventory("pkg.Thing"))
    )
    other_route = respx_mock.get(OTHER_INVENTORY_URL).mock(
        return_value=Response(200, content=_inventory("other.Thing"))
    )
    await _register(client, url=INVENTORY_URL, title="A docs")
    await _register(client, url=OTHER_INVENTORY_URL, title="B docs")

    response = await client.post(
        INGEST_URL, json={"source_url": INVENTORY_URL}
    )

    assert response.status_code == 200
    data = response.json()
    assert [source["url"] for source in data["sources"]] == [INVENTORY_URL]
    assert other_route.call_count == 0


@pytest.mark.asyncio
async def test_post_ingest_intersphinx_reports_a_failing_source(
    client: AsyncClient, respx_mock: respx.Router
) -> None:
    """A site that cannot be read is reported in the body, not as an error.

    The run's job is to refresh every site it can; a single unreachable
    origin is that site's news, so it is reported per source rather than
    turned into a status code that says nothing about the others.
    """
    respx_mock.get(INVENTORY_URL).mock(return_value=Response(503))
    await _register(client, url=INVENTORY_URL, title="A docs")

    response = await client.post(INGEST_URL)

    assert response.status_code == 200
    data = response.json()
    assert data["failure_count"] == 1
    assert data["sources"][0]["status"] == "failure"
    assert data["sources"][0]["error"] is not None


@pytest.mark.asyncio
async def test_post_ingest_intersphinx_rejects_an_unregistered_url(
    client: AsyncClient,
) -> None:
    """An inventory URL nobody registered is a 404."""
    response = await client.post(
        INGEST_URL, json={"source_url": INVENTORY_URL}
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_openapi_documents_the_admin_scope_on_the_trigger(
    client: AsyncClient,
) -> None:
    """The manual trigger publishes the scope the ingress enforces.

    Nothing in Ook enforces it -- it is Gafaelfawr ingress configuration,
    as everywhere else in the app -- so the published API is the only place
    the requirement can be written down.
    """
    response = await client.get(f"{config.path_prefix}/openapi.json")
    assert response.status_code == 200
    operation = response.json()["paths"][INGEST_URL]["post"]

    assert "exec:admin" in operation["description"]
