"""Tests for the ``/ook/links/domains/python`` endpoints."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urljoin

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

OBJECT_PATH_TEMPLATE = f"{OBJECTS_URL}/{{name}}"
"""The single-object route's path as OpenAPI keys it."""

INVENTORY_URL = "https://pipelines.lsst.io/v/weekly/objects.inv"
"""The inventory URL of the source seeded by these tests."""

SOURCE_TITLE = "Rubin Science Pipelines"
"""The registered source's human title, which links carry as their
collection title.
"""


def _entity(name: str, *, role: str, uri: str) -> InventoryEntity:
    """Build one entity as an inventory would declare it.

    ``parent_name`` is always None because it is never what decides the
    stored hierarchy: containment is derived from the links, which is what
    `_seed_pipelines_source` sets up.
    """
    return InventoryEntity(
        sphinx_domain="py",
        role=role,
        name=name,
        display_name=name,
        uri=uri,
        parent_name=None,
    )


MODULE = _entity(
    "lsst.afw.table", role="module", uri="py-api/lsst.afw.table.html"
)
"""The module these tests hang a hierarchy off."""

SOURCE_CATALOG = _entity(
    "lsst.afw.table.SourceCatalog",
    role="class",
    uri="py-api/lsst.afw.table.SourceCatalog.html#anchor",
)
"""A class inside `MODULE`."""

SOURCE_CATALOG_URL = (
    "https://pipelines.lsst.io/v/weekly/py-api/"
    "lsst.afw.table.SourceCatalog.html#anchor"
)
"""Where `SOURCE_CATALOG`'s link points, resolved against the inventory."""


def _link(entity_id: int, entity: InventoryEntity) -> IntersphinxSourceLink:
    """Build the link a site contributes for one entity."""
    return IntersphinxSourceLink(
        entity_id=entity_id,
        html_url=urljoin(INVENTORY_URL, entity.uri),
        title=entity.name,
        type="python_api",
    )


async def _seed_pipelines_source(
    factory: Factory,
    entities: Sequence[InventoryEntity] = (MODULE, SOURCE_CATALOG),
) -> None:
    """Register a documentation source and let it document *entities*.

    Seeding runs through the stores rather than through an ingest, so these
    tests exercise the read path alone: an ingest failure would otherwise
    read here as a Links API failure. It still leaves the stores in the
    state an ingest leaves them in -- every entity documented by a source,
    and containment derived from those links afterwards -- because that is
    the state the endpoints answer from.
    """
    async with factory.db_session.begin():
        source = await factory.create_intersphinx_source_store().add_source(
            url=INVENTORY_URL, title=SOURCE_TITLE
        )
        entity_store = factory.create_intersphinx_entity_store()
        entity_ids = await entity_store.upsert_entities(entities)
        await entity_store.replace_source_links(
            source.id,
            [
                _link(entity_ids["py", entity.name], entity)
                for entity in entities
            ],
            collection_title=source.title,
        )
        await entity_store.recompute_containment()


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
    assert data["collections"]["children"].endswith(
        "/ook/links/domains/python/objects/{name}/children"
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
            "url": SOURCE_CATALOG_URL,
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
async def test_an_object_no_site_documents_is_not_found(
    client: AsyncClient, factory: Factory
) -> None:
    """An object every site has dropped answers as an unknown name.

    Ook stores an object only while some site gives it a page, so there is
    no "known, but nobody documents it" answer for this endpoint to make:
    the module is gone as soon as its last link is.
    """
    await _seed_pipelines_source(factory, [SOURCE_CATALOG])

    response = await client.get(f"{OBJECTS_URL}/lsst.afw.table")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_python_object_links_carry_the_domain_type_header(
    client: AsyncClient, factory: Factory
) -> None:
    """The single-object response names the object's role in a header.

    The body is a bare list of links with nowhere to say what kind of thing
    was linked to, so the one scalar a client needs -- the Sphinx role --
    rides alongside it.
    """
    await _seed_pipelines_source(factory)

    response = await client.get(f"{OBJECTS_URL}/lsst.afw.table.SourceCatalog")

    assert response.status_code == 200
    assert response.headers["X-Ook-Entity-Domain-Type"] == "class"


@pytest.mark.asyncio
async def test_python_object_domain_type_header_is_the_stored_role(
    client: AsyncClient, factory: Factory
) -> None:
    """The header reports each object's own role, not a fixed value."""
    await _seed_pipelines_source(factory)

    response = await client.get(f"{OBJECTS_URL}/lsst.afw.table")

    assert response.status_code == 200
    assert response.headers["X-Ook-Entity-Domain-Type"] == "module"


@pytest.mark.asyncio
async def test_python_object_domain_type_header_matches_the_collection(
    client: AsyncClient, factory: Factory
) -> None:
    """The header and the collection's ``domain_type`` agree on a name.

    They are two views of one stored role, so a client that read a listing
    and then followed a ``self_url`` must not be told two different things
    about the same object.
    """
    await _seed_pipelines_source(factory)

    collection = await client.get(OBJECTS_URL)
    assert collection.status_code == 200
    from_collection = {
        entry["entity"]["name"]: entry["entity"]["domain_type"]
        for entry in collection.json()
    }

    from_header = {}
    for name in from_collection:
        response = await client.get(f"{OBJECTS_URL}/{name}")
        assert response.status_code == 200
        from_header[name] = response.headers["X-Ook-Entity-Domain-Type"]

    assert from_header == from_collection


@pytest.mark.asyncio
async def test_unknown_python_object_carries_no_domain_type_header(
    client: AsyncClient, factory: Factory
) -> None:
    """A 404 names no entity, so it carries no entity header."""
    await _seed_pipelines_source(factory)

    response = await client.get(f"{OBJECTS_URL}/lsst.afw.table.NoSuchClass")

    assert response.status_code == 404
    assert "X-Ook-Entity-Domain-Type" not in response.headers


@pytest.mark.asyncio
async def test_openapi_documents_the_domain_type_header(
    client: AsyncClient,
) -> None:
    """The header is part of the published contract, not folklore.

    A header a client is expected to read has to be discoverable from the
    schema, and only on the shape that actually carries it: the 404 names
    no entity.
    """
    response = await client.get(f"{config.path_prefix}/openapi.json")

    assert response.status_code == 200
    operation = response.json()["paths"][OBJECT_PATH_TEMPLATE]["get"]

    documented = operation["responses"]["200"]["headers"]
    assert documented["X-Ook-Entity-Domain-Type"]["schema"] == {
        "type": "string"
    }
    assert "headers" not in operation["responses"]["404"]


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
    assert data[0]["entity"]["domain_type"] == "module"
    assert data[0]["entity"]["self_url"].endswith(
        "/ook/links/domains/python/objects/lsst.afw.table"
    )
    assert data[0]["links"] == [
        {
            "url": "https://pipelines.lsst.io/v/weekly/py-api/"
            "lsst.afw.table.html",
            "title": "lsst.afw.table",
            "type": "python_api",
            "collection_title": SOURCE_TITLE,
        }
    ]
    assert data[1]["links"] == [
        {
            "url": SOURCE_CATALOG_URL,
            "title": "lsst.afw.table.SourceCatalog",
            "type": "python_api",
            "collection_title": SOURCE_TITLE,
        }
    ]


@pytest.mark.asyncio
async def test_python_objects_collection_counts_only_documented_objects(
    client: AsyncClient, factory: Factory
) -> None:
    """The total counts what the sites document, with nothing held in place.

    The class's module is not documented here, so it is not an object of
    the domain at all -- and the collection's count, which is what a client
    pages against, says so.
    """
    await _seed_pipelines_source(factory, [SOURCE_CATALOG])

    response = await client.get(OBJECTS_URL)

    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "1"
    assert [entry["entity"]["name"] for entry in response.json()] == [
        "lsst.afw.table.SourceCatalog"
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


@pytest.mark.asyncio
async def test_python_object_children(
    client: AsyncClient, factory: Factory
) -> None:
    """A module's children endpoint lists its members with their links."""
    await _seed_pipelines_source(factory)

    response = await client.get(f"{OBJECTS_URL}/lsst.afw.table/children")

    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "1"
    assert "Link" in response.headers
    data = response.json()
    assert [entry["entity"]["name"] for entry in data] == [
        "lsst.afw.table.SourceCatalog"
    ]
    assert data[0]["entity"]["domain"] == "python"
    assert data[0]["entity"]["domain_type"] == "class"
    assert data[0]["entity"]["self_url"].endswith(
        "/ook/links/domains/python/objects/lsst.afw.table.SourceCatalog"
    )
    assert data[0]["links"] == [
        {
            "url": SOURCE_CATALOG_URL,
            "title": "lsst.afw.table.SourceCatalog",
            "type": "python_api",
            "collection_title": SOURCE_TITLE,
        }
    ]


@pytest.mark.asyncio
async def test_python_object_children_excludes_grandchildren(
    client: AsyncClient, factory: Factory
) -> None:
    """A module lists its classes, not the methods inside those classes."""
    await _seed_pipelines_source(
        factory,
        [
            MODULE,
            SOURCE_CATALOG,
            _entity(
                "lsst.afw.table.SourceCatalog.find",
                role="method",
                uri="py-api/lsst.afw.table.SourceCatalog.html#find",
            ),
        ],
    )

    response = await client.get(f"{OBJECTS_URL}/lsst.afw.table/children")

    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "1"
    assert [entry["entity"]["name"] for entry in response.json()] == [
        "lsst.afw.table.SourceCatalog"
    ]


@pytest.mark.asyncio
async def test_python_object_children_of_a_leaf_is_empty(
    client: AsyncClient, factory: Factory
) -> None:
    """An object that contains nothing answers with an empty page."""
    await _seed_pipelines_source(factory)

    response = await client.get(
        f"{OBJECTS_URL}/lsst.afw.table.SourceCatalog/children"
    )

    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"


@pytest.mark.asyncio
async def test_python_object_children_of_unknown_object_is_not_found(
    client: AsyncClient, factory: Factory
) -> None:
    """A name no stored Python object answers to is a 404.

    The distinction the empty page above cannot make: nothing here is
    known, rather than known and empty.
    """
    await _seed_pipelines_source(factory)

    response = await client.get(
        f"{OBJECTS_URL}/lsst.afw.table.NoSuchClass/children"
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_python_object_children_pages(
    client: AsyncClient, factory: Factory
) -> None:
    """Following the Link header's next URL yields the following children."""
    await _seed_pipelines_source(
        factory,
        [
            MODULE,
            SOURCE_CATALOG,
            _entity(
                "lsst.afw.table.BaseCatalog",
                role="class",
                uri="py-api/lsst.afw.table.BaseCatalog.html",
            ),
        ],
    )

    response = await client.get(
        f"{OBJECTS_URL}/lsst.afw.table/children", params={"limit": 1}
    )

    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "2"
    assert [entry["entity"]["name"] for entry in response.json()] == [
        "lsst.afw.table.BaseCatalog"
    ]
    links = PaginationLinkData.from_header(response.headers["Link"])
    assert links.next_url is not None

    next_response = await client.get(links.next_url)

    assert next_response.status_code == 200
    assert [entry["entity"]["name"] for entry in next_response.json()] == [
        "lsst.afw.table.SourceCatalog"
    ]


@pytest.mark.asyncio
async def test_python_object_domain_type_is_the_sphinx_role(
    client: AsyncClient, factory: Factory
) -> None:
    """Each object reports the role its inventory declared it with.

    The role is the whole point of the field: a client filtering a listing
    down to classes cannot do it if every object says ``object``.
    """
    await _seed_pipelines_source(factory)

    response = await client.get(OBJECTS_URL)

    assert response.status_code == 200
    data = response.json()
    assert {
        entry["entity"]["name"]: entry["entity"]["domain_type"]
        for entry in data
    } == {
        "lsst.afw.table": "module",
        "lsst.afw.table.SourceCatalog": "class",
    }


@pytest.mark.asyncio
async def test_python_object_children_domain_type_is_the_sphinx_role(
    client: AsyncClient, factory: Factory
) -> None:
    """A child reports its own role, not the collection's."""
    await _seed_pipelines_source(factory)

    response = await client.get(f"{OBJECTS_URL}/lsst.afw.table/children")

    assert response.status_code == 200
    data = response.json()
    assert [entry["entity"]["domain_type"] for entry in data] == ["class"]


@pytest.mark.asyncio
async def test_python_objects_filter_by_domain_type(
    client: AsyncClient, factory: Factory
) -> None:
    """A ``domain_type`` value narrows the listing and its total count."""
    await _seed_pipelines_source(factory)

    response = await client.get(OBJECTS_URL, params={"domain_type": "class"})

    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "1"
    assert [entry["entity"]["name"] for entry in response.json()] == [
        "lsst.afw.table.SourceCatalog"
    ]


@pytest.mark.asyncio
async def test_python_objects_filter_ors_several_domain_types(
    client: AsyncClient, factory: Factory
) -> None:
    """Repeating the parameter admits an object carrying any of the roles."""
    await _seed_pipelines_source(
        factory,
        [
            MODULE,
            SOURCE_CATALOG,
            _entity(
                "lsst.afw.table.SourceCatalog.find",
                role="method",
                uri="py-api/lsst.afw.table.SourceCatalog.html#find",
            ),
        ],
    )

    response = await client.get(
        OBJECTS_URL,
        params=[("domain_type", "module"), ("domain_type", "method")],
    )

    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "2"
    assert [entry["entity"]["name"] for entry in response.json()] == [
        "lsst.afw.table",
        "lsst.afw.table.SourceCatalog.find",
    ]


@pytest.mark.asyncio
async def test_python_objects_filter_on_an_unknown_domain_type_is_empty(
    client: AsyncClient, factory: Factory
) -> None:
    """A role nothing carries is an empty page rather than a 422.

    The vocabulary is whatever Sphinx and its extensions emit, so an
    unrecognized value is a question with no matches rather than a
    malformed request.
    """
    await _seed_pipelines_source(factory)

    response = await client.get(
        OBJECTS_URL, params={"domain_type": "pydantic_model"}
    )

    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"


@pytest.mark.asyncio
async def test_python_objects_filter_rejects_an_empty_domain_type(
    client: AsyncClient, factory: Factory
) -> None:
    """An empty value names no role at all, so it is a 422."""
    await _seed_pipelines_source(factory)

    response = await client.get(OBJECTS_URL, params={"domain_type": ""})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_python_objects_filter_holds_across_pages(
    client: AsyncClient, factory: Factory
) -> None:
    """The next page of a filtered listing is still filtered.

    The cursor carries the ordering key alone, so the filter rides in the
    query string the ``Link`` header preserves.
    """
    await _seed_pipelines_source(
        factory,
        [
            MODULE,
            SOURCE_CATALOG,
            _entity(
                "lsst.afw.table.BaseCatalog",
                role="class",
                uri="py-api/lsst.afw.table.BaseCatalog.html",
            ),
        ],
    )

    response = await client.get(
        OBJECTS_URL, params={"domain_type": "class", "limit": 1}
    )

    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "2"
    assert [entry["entity"]["name"] for entry in response.json()] == [
        "lsst.afw.table.BaseCatalog"
    ]
    links = PaginationLinkData.from_header(response.headers["Link"])
    assert links.next_url is not None

    next_response = await client.get(links.next_url)

    assert next_response.status_code == 200
    assert next_response.headers["X-Total-Count"] == "2"
    assert [entry["entity"]["name"] for entry in next_response.json()] == [
        "lsst.afw.table.SourceCatalog"
    ]


@pytest.mark.asyncio
async def test_python_object_children_filter_by_domain_type(
    client: AsyncClient, factory: Factory
) -> None:
    """A ``domain_type`` value narrows a parent's children the same way."""
    await _seed_pipelines_source(
        factory,
        [
            MODULE,
            SOURCE_CATALOG,
            _entity(
                "lsst.afw.table.makeCatalog",
                role="function",
                uri="py-api/lsst.afw.table.makeCatalog.html",
            ),
        ],
    )

    response = await client.get(
        f"{OBJECTS_URL}/lsst.afw.table/children",
        params={"domain_type": "function"},
    )

    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "1"
    assert [entry["entity"]["name"] for entry in response.json()] == [
        "lsst.afw.table.makeCatalog"
    ]


@pytest.mark.asyncio
async def test_python_object_children_filter_on_unknown_type_is_empty(
    client: AsyncClient, factory: Factory
) -> None:
    """A filter matching none of a known parent's children is still a 200."""
    await _seed_pipelines_source(factory)

    response = await client.get(
        f"{OBJECTS_URL}/lsst.afw.table/children",
        params={"domain_type": "exception"},
    )

    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"


@pytest.mark.asyncio
async def test_python_object_children_filter_rejects_an_empty_value(
    client: AsyncClient, factory: Factory
) -> None:
    """An empty value is a 422 on the children endpoint too."""
    await _seed_pipelines_source(factory)

    response = await client.get(
        f"{OBJECTS_URL}/lsst.afw.table/children", params={"domain_type": ""}
    )

    assert response.status_code == 422
