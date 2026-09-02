"""Tests for the ``/ook/links/domains/python`` endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from safir.http import PaginationLinkData

from ook.config import config
from ook.domain.intersphinxentities import (
    IntersphinxSourceLink,
    InventoryEntity,
)
from ook.factory import Factory

DOMAIN_URL = f"{config.path_prefix}/links/domains/python"
"""The python link domain's info endpoint."""

OBJECTS_URL = f"{DOMAIN_URL}/objects"
"""The python link domain's object entity endpoint."""

INVENTORY_URL = "https://pipelines.lsst.io/v/weekly/objects.inv"
"""The inventory URL of the source seeded by these tests."""

SOURCE_TITLE = "Rubin Science Pipelines"
"""The registered source's human title, which links carry as their
collection title.
"""


async def _seed_pipelines_source(factory: Factory) -> None:
    """Register a documentation source and give it one documented class.

    Seeding runs through the stores rather than through an ingest, so these
    tests exercise the read path alone: an ingest failure would otherwise
    read here as a Links API failure.
    """
    async with factory.db_session.begin():
        source = await factory.create_intersphinx_source_store().add_source(
            url=INVENTORY_URL, title=SOURCE_TITLE
        )
        entity_store = factory.create_intersphinx_entity_store()
        entity_ids = await entity_store.upsert_entities(
            [
                InventoryEntity(
                    sphinx_domain="py",
                    role="module",
                    name="lsst.afw.table",
                    dispname="lsst.afw.table",
                    uri="py-api/lsst.afw.table.html",
                    parent_name=None,
                ),
                InventoryEntity(
                    sphinx_domain="py",
                    role="class",
                    name="lsst.afw.table.SourceCatalog",
                    dispname="lsst.afw.table.SourceCatalog",
                    uri="py-api/lsst.afw.table.SourceCatalog.html#anchor",
                    parent_name="lsst.afw.table",
                ),
            ]
        )
        await entity_store.replace_source_links(
            source.id,
            [
                IntersphinxSourceLink(
                    entity_id=entity_ids["py", "lsst.afw.table.SourceCatalog"],
                    html_url=(
                        "https://pipelines.lsst.io/v/weekly/py-api/"
                        "lsst.afw.table.SourceCatalog.html#anchor"
                    ),
                    title="lsst.afw.table.SourceCatalog",
                    type="python_api",
                )
            ],
            collection_title=source.title,
        )


@pytest.mark.asyncio
async def test_python_domain_info(client: AsyncClient) -> None:
    """The domain info endpoint advertises the domain's URI templates."""
    response = await client.get(DOMAIN_URL)

    assert response.status_code == 200
    data = response.json()
    # The same two-key shape the SDM domain publishes, so a client can read
    # either domain's info without knowing which one it asked for.
    assert set(data) == {"entities", "collections"}
    assert data["entities"]["object"].endswith(
        "/ook/links/domains/python/objects/{name}"
    )
    assert data["collections"]["objects"].endswith(
        "/ook/links/domains/python/objects"
    )


@pytest.mark.asyncio
async def test_python_object_links(
    client: AsyncClient, factory: Factory
) -> None:
    """An object's links carry the registered source's title."""
    await _seed_pipelines_source(factory)

    response = await client.get(f"{OBJECTS_URL}/lsst.afw.table.SourceCatalog")

    assert response.status_code == 200
    assert response.json() == [
        {
            "url": (
                "https://pipelines.lsst.io/v/weekly/py-api/"
                "lsst.afw.table.SourceCatalog.html#anchor"
            ),
            "title": "lsst.afw.table.SourceCatalog",
            "type": "python_api",
            "collection_title": SOURCE_TITLE,
        }
    ]


@pytest.mark.asyncio
async def test_unknown_python_object_is_not_found(
    client: AsyncClient, factory: Factory
) -> None:
    """A name no stored Python object answers to is a 404."""
    await _seed_pipelines_source(factory)

    response = await client.get(f"{OBJECTS_URL}/lsst.afw.table.NoSuchClass")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_python_object_without_links(
    client: AsyncClient, factory: Factory
) -> None:
    """An object no source gives a page answers with an empty list.

    The module here is held in place only by the class beneath it, and
    saying so is the point: an empty list means "known, but undocumented",
    which the 404 of an unknown name cannot express.
    """
    await _seed_pipelines_source(factory)

    response = await client.get(f"{OBJECTS_URL}/lsst.afw.table")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_python_objects_collection(
    client: AsyncClient, factory: Factory
) -> None:
    """The collection lists every object with its links and a total count."""
    await _seed_pipelines_source(factory)

    response = await client.get(OBJECTS_URL)

    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "2"
    assert "Link" in response.headers
    data = response.json()
    assert [entry["entity"]["name"] for entry in data] == [
        "lsst.afw.table",
        "lsst.afw.table.SourceCatalog",
    ]
    assert data[0]["entity"]["domain"] == "python"
    assert data[0]["entity"]["domain_type"] == "object"
    assert data[0]["entity"]["self_url"].endswith(
        "/ook/links/domains/python/objects/lsst.afw.table"
    )
    # The module holds no page of its own, and the collection says so the
    # same way the single-object endpoint does: an empty list, not a gap.
    assert data[0]["links"] == []
    assert data[1]["links"] == [
        {
            "url": (
                "https://pipelines.lsst.io/v/weekly/py-api/"
                "lsst.afw.table.SourceCatalog.html#anchor"
            ),
            "title": "lsst.afw.table.SourceCatalog",
            "type": "python_api",
            "collection_title": SOURCE_TITLE,
        }
    ]


@pytest.mark.asyncio
async def test_python_objects_collection_pages(
    client: AsyncClient, factory: Factory
) -> None:
    """Following the Link header's next URL yields the following objects."""
    await _seed_pipelines_source(factory)

    response = await client.get(OBJECTS_URL, params={"limit": 1})

    assert response.status_code == 200
    first = response.json()
    assert [entry["entity"]["name"] for entry in first] == ["lsst.afw.table"]
    links = PaginationLinkData.from_header(response.headers["Link"])
    assert links.next_url is not None

    next_response = await client.get(links.next_url)

    assert next_response.status_code == 200
    assert [entry["entity"]["name"] for entry in next_response.json()] == [
        "lsst.afw.table.SourceCatalog"
    ]


@pytest.mark.asyncio
async def test_python_objects_collection_is_empty_when_nothing_ingested(
    client: AsyncClient,
) -> None:
    """An unpopulated domain is an empty collection, not a 404."""
    response = await client.get(OBJECTS_URL)

    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"
