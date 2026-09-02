"""Tests for the IntersphinxEntityStore."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ook.dbschema.links import SqlIntersphinxLink, SqlLink
from ook.domain.intersphinxentities import InventoryEntity
from ook.domain.links import Link
from ook.factory import Factory


def _entity(
    name: str,
    *,
    role: str = "class",
    dispname: str | None = None,
    uri: str = "py-api/index.html#anchor",
    parent_name: str | None = None,
    sphinx_domain: str = "py",
) -> InventoryEntity:
    return InventoryEntity(
        sphinx_domain=sphinx_domain,
        role=role,
        name=name,
        dispname=dispname or name,
        uri=uri,
        parent_name=parent_name,
    )


@pytest.mark.asyncio
async def test_upsert_entities_resolves_parent(factory: Factory) -> None:
    """A parent named in the same batch is resolved to its entity."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_entity_store()

        await store.upsert_entities(
            [
                _entity("lsst.afw.table", role="module"),
                _entity(
                    "lsst.afw.table.SourceCatalog",
                    parent_name="lsst.afw.table",
                ),
            ]
        )

        child = await store.get_entity("py", "lsst.afw.table.SourceCatalog")
        assert child is not None
        assert child.name == "lsst.afw.table.SourceCatalog"
        assert child.role == "class"
        assert child.dispname == "lsst.afw.table.SourceCatalog"
        assert child.parent_name == "lsst.afw.table"
        assert child.extras is None
        assert child.links == []


@pytest.mark.asyncio
async def test_upsert_entities_unknown_parent(factory: Factory) -> None:
    """A parent no stored entity answers to leaves the child top level."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_entity_store()

        await store.upsert_entities(
            [
                _entity(
                    "lsst.afw.table.SourceCatalog",
                    parent_name="lsst.afw.table",
                )
            ]
        )

        child = await store.get_entity("py", "lsst.afw.table.SourceCatalog")
        assert child is not None
        assert child.parent_name is None


@pytest.mark.asyncio
async def test_upsert_entities_parent_from_earlier_batch(
    factory: Factory,
) -> None:
    """A parent stored by an earlier upsert is still resolved."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_entity_store()
        await store.upsert_entities([_entity("lsst.afw.table", role="module")])

        await store.upsert_entities(
            [
                _entity(
                    "lsst.afw.table.SourceCatalog",
                    parent_name="lsst.afw.table",
                )
            ]
        )

        child = await store.get_entity("py", "lsst.afw.table.SourceCatalog")
        assert child is not None
        assert child.parent_name == "lsst.afw.table"


@pytest.mark.asyncio
async def test_upsert_entities_parent_is_per_domain(factory: Factory) -> None:
    """A parent of the same name in another Sphinx domain is not borrowed."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_entity_store()

        await store.upsert_entities(
            [
                _entity("lsst.afw.table", role="label", sphinx_domain="std"),
                _entity(
                    "lsst.afw.table.SourceCatalog",
                    parent_name="lsst.afw.table",
                ),
            ]
        )

        child = await store.get_entity("py", "lsst.afw.table.SourceCatalog")
        assert child is not None
        assert child.parent_name is None


@pytest.mark.asyncio
async def test_upsert_entities_updates_in_place(factory: Factory) -> None:
    """Re-upserting an identity updates its role and display name."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_entity_store()
        first = await store.upsert_entities(
            [_entity("lsst.afw.table.SourceCatalog", role="class")]
        )

        second = await store.upsert_entities(
            [
                _entity(
                    "lsst.afw.table.SourceCatalog",
                    role="attribute",
                    dispname="SourceCatalog",
                )
            ]
        )

        assert second == first
        stored = await store.get_entity("py", "lsst.afw.table.SourceCatalog")
        assert stored is not None
        assert stored.role == "attribute"
        assert stored.dispname == "SourceCatalog"


@pytest.mark.asyncio
async def test_upsert_entities_keeps_parent_when_unnamed(
    factory: Factory,
) -> None:
    """A source that omits the parent does not withdraw an existing one."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_entity_store()
        await store.upsert_entities(
            [
                _entity("lsst.afw.table", role="module"),
                _entity(
                    "lsst.afw.table.SourceCatalog",
                    parent_name="lsst.afw.table",
                ),
            ]
        )

        await store.upsert_entities(
            [_entity("lsst.afw.table.SourceCatalog", parent_name=None)]
        )

        child = await store.get_entity("py", "lsst.afw.table.SourceCatalog")
        assert child is not None
        assert child.parent_name == "lsst.afw.table"


@pytest.mark.asyncio
async def test_upsert_entities_merges_duplicate_identity(
    factory: Factory,
) -> None:
    """A name declared twice in one batch stores once, first declaration."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_entity_store()

        entity_ids = await store.upsert_entities(
            [
                _entity("lsst.afw.table.SourceCatalog", role="class"),
                _entity("lsst.afw.table.SourceCatalog", role="attribute"),
            ]
        )

        assert len(entity_ids) == 1
        stored = await store.get_entity("py", "lsst.afw.table.SourceCatalog")
        assert stored is not None
        assert stored.role == "class"


@pytest.mark.asyncio
async def test_get_entity_unknown(factory: Factory) -> None:
    """An unstored name resolves to None rather than an empty entity."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_entity_store()

        assert await store.get_entity("py", "nothing.here") is None


async def _add_link(
    factory: Factory,
    *,
    entity_id: int,
    source_id: int,
    html_url: str,
    source_title: str,
    collection_title: str,
) -> None:
    """Insert one intersphinx link row.

    The ingest service's per-source link replace lands with the slice that
    uses it, so this stands in for it here: the store's read path and the
    polymorphic mapping are what these tests are about.
    """
    factory.db_session.add(
        SqlIntersphinxLink(
            entity_id=entity_id,
            source_id=source_id,
            html_url=html_url,
            source_type="Python API",
            source_title=source_title,
            source_collection_title=collection_title,
            date_updated=datetime.now(tz=UTC).replace(microsecond=0),
        )
    )
    await factory.db_session.flush()


@pytest.mark.asyncio
async def test_get_entity_returns_links(factory: Factory) -> None:
    """An entity's links come back with the source's title as the
    collection title.
    """
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        source_store = factory.create_intersphinx_source_store()
        source = await source_store.add_source(
            url="https://pipelines.lsst.io/objects.inv",
            title="LSST Science Pipelines",
        )
        entity_ids = await entity_store.upsert_entities(
            [_entity("lsst.afw.table.SourceCatalog")]
        )
        await _add_link(
            factory,
            entity_id=entity_ids["py", "lsst.afw.table.SourceCatalog"],
            source_id=source.id,
            html_url="https://pipelines.lsst.io/py-api/index.html#anchor",
            source_title="SourceCatalog",
            collection_title=source.title,
        )

        stored = await entity_store.get_entity(
            "py", "lsst.afw.table.SourceCatalog"
        )

        assert stored is not None
        assert stored.links == [
            Link(
                html_url=(
                    "https://pipelines.lsst.io/py-api/index.html#anchor"
                ),
                type="Python API",
                title="SourceCatalog",
                collection_title="LSST Science Pipelines",
            )
        ]


@pytest.mark.asyncio
async def test_link_loads_through_polymorphic_query(
    factory: Factory,
) -> None:
    """A link row loads as its own subtype from the base link query."""
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        source_store = factory.create_intersphinx_source_store()
        source = await source_store.add_source(
            url="https://pipelines.lsst.io/objects.inv",
            title="LSST Science Pipelines",
        )
        entity_ids = await entity_store.upsert_entities(
            [_entity("lsst.afw.table.SourceCatalog")]
        )
        await _add_link(
            factory,
            entity_id=entity_ids["py", "lsst.afw.table.SourceCatalog"],
            source_id=source.id,
            html_url="https://pipelines.lsst.io/py-api/index.html#anchor",
            source_title="SourceCatalog",
            collection_title=source.title,
        )

        link = (await factory.db_session.execute(select(SqlLink))).scalar_one()

        assert isinstance(link, SqlIntersphinxLink)
        assert link.type == "intersphinx"
        assert link.html_url == (
            "https://pipelines.lsst.io/py-api/index.html#anchor"
        )


@pytest.mark.asyncio
async def test_link_joins_entity_and_source(factory: Factory) -> None:
    """A link row resolves to both the entity and the source it names."""
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        source_store = factory.create_intersphinx_source_store()
        source = await source_store.add_source(
            url="https://pipelines.lsst.io/objects.inv",
            title="LSST Science Pipelines",
        )
        entity_ids = await entity_store.upsert_entities(
            [_entity("lsst.afw.table.SourceCatalog")]
        )
        await _add_link(
            factory,
            entity_id=entity_ids["py", "lsst.afw.table.SourceCatalog"],
            source_id=source.id,
            html_url="https://pipelines.lsst.io/py-api/index.html#anchor",
            source_title="SourceCatalog",
            collection_title=source.title,
        )

        link = (
            await factory.db_session.execute(
                select(SqlIntersphinxLink).options(
                    selectinload(SqlIntersphinxLink.entity),
                    selectinload(SqlIntersphinxLink.source),
                )
            )
        ).scalar_one()

        assert link.entity.name == "lsst.afw.table.SourceCatalog"
        assert link.source.url == "https://pipelines.lsst.io/objects.inv"


@pytest.mark.asyncio
async def test_deleting_source_deletes_its_links(factory: Factory) -> None:
    """Deleting a source cascades to its links but keeps the entity."""
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        source_store = factory.create_intersphinx_source_store()
        source = await source_store.add_source(
            url="https://pipelines.lsst.io/objects.inv",
            title="LSST Science Pipelines",
        )
        entity_ids = await entity_store.upsert_entities(
            [_entity("lsst.afw.table.SourceCatalog")]
        )
        await _add_link(
            factory,
            entity_id=entity_ids["py", "lsst.afw.table.SourceCatalog"],
            source_id=source.id,
            html_url="https://pipelines.lsst.io/py-api/index.html#anchor",
            source_title="SourceCatalog",
            collection_title=source.title,
        )

        await source_store.delete_source(source.id)

        stored = await entity_store.get_entity(
            "py", "lsst.afw.table.SourceCatalog"
        )
        assert stored is not None
        assert stored.links == []
