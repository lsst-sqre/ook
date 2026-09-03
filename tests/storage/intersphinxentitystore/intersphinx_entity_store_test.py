"""Tests for the IntersphinxEntityStore."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ook.dbschema.links import SqlIntersphinxLink, SqlLink
from ook.domain.intersphinxentities import (
    IntersphinxSourceLink,
    InventoryEntity,
)
from ook.domain.links import Link
from ook.factory import Factory


def _entity(
    name: str,
    *,
    role: str = "class",
    display_name: str | None = None,
    uri: str = "py-api/index.html#anchor",
    parent_name: str | None = None,
    sphinx_domain: str = "py",
) -> InventoryEntity:
    return InventoryEntity(
        sphinx_domain=sphinx_domain,
        role=role,
        name=name,
        display_name=display_name or name,
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
        assert child.display_name == "lsst.afw.table.SourceCatalog"
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
                    display_name="SourceCatalog",
                )
            ]
        )

        assert second == first
        stored = await store.get_entity("py", "lsst.afw.table.SourceCatalog")
        assert stored is not None
        assert stored.role == "attribute"
        assert stored.display_name == "SourceCatalog"


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
async def test_get_entity_orders_links_by_site_title(
    factory: Factory,
) -> None:
    """An entity's links come back sorted by the site they point into.

    The two sources are set up so that site title and URL disagree about
    the order, which is what separates a site-sorted list from one that
    merely falls out of the URL tiebreaker.
    """
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        source_store = factory.create_intersphinx_source_store()
        zebra = await source_store.add_source(
            url="https://a.example/objects.inv", title="Zebra docs"
        )
        alpha = await source_store.add_source(
            url="https://z.example/objects.inv", title="Alpha docs"
        )
        entity_ids = await entity_store.upsert_entities([_entity("pkg.Thing")])
        entity_id = entity_ids["py", "pkg.Thing"]
        for source, host in ((zebra, "a.example"), (alpha, "z.example")):
            await entity_store.replace_source_links(
                source.id,
                [
                    _source_link(
                        entity_id,
                        html_url=f"https://{host}/api.html#pkg.Thing",
                    )
                ],
                collection_title=source.title,
            )

        stored = await entity_store.get_entity("py", "pkg.Thing")

        assert stored is not None
        assert [link.collection_title for link in stored.links] == [
            "Alpha docs",
            "Zebra docs",
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


def _source_link(
    entity_id: int,
    *,
    html_url: str,
    title: str = "pkg.Thing",
    link_type: str = "python_api",
) -> IntersphinxSourceLink:
    return IntersphinxSourceLink(
        entity_id=entity_id,
        html_url=html_url,
        title=title,
        type=link_type,
    )


@pytest.mark.asyncio
async def test_replace_source_links_writes_the_links(
    factory: Factory,
) -> None:
    """A replace writes the links with the source's title as the collection
    title.
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

        written = await entity_store.replace_source_links(
            source.id,
            [
                _source_link(
                    entity_ids["py", "lsst.afw.table.SourceCatalog"],
                    html_url=(
                        "https://pipelines.lsst.io/py-api/index.html#anchor"
                    ),
                    title="lsst.afw.table.SourceCatalog",
                )
            ],
            collection_title=source.title,
        )

        assert written == 1
        stored = await entity_store.get_entity(
            "py", "lsst.afw.table.SourceCatalog"
        )
        assert stored is not None
        assert stored.links == [
            Link(
                html_url=(
                    "https://pipelines.lsst.io/py-api/index.html#anchor"
                ),
                type="python_api",
                title="lsst.afw.table.SourceCatalog",
                collection_title="LSST Science Pipelines",
            )
        ]


@pytest.mark.asyncio
async def test_replace_source_links_leaves_other_sources_alone(
    factory: Factory,
) -> None:
    """Re-ingesting one site replaces its links and no one else's."""
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        source_store = factory.create_intersphinx_source_store()
        first = await source_store.add_source(
            url="https://a.example/objects.inv", title="A docs"
        )
        second = await source_store.add_source(
            url="https://b.example/objects.inv", title="B docs"
        )
        entity_ids = await entity_store.upsert_entities([_entity("pkg.Thing")])
        entity_id = entity_ids["py", "pkg.Thing"]
        await entity_store.replace_source_links(
            first.id,
            [_source_link(entity_id, html_url="https://a.example/old.html")],
            collection_title=first.title,
        )
        await entity_store.replace_source_links(
            second.id,
            [_source_link(entity_id, html_url="https://b.example/keep.html")],
            collection_title=second.title,
        )

        await entity_store.replace_source_links(
            first.id,
            [_source_link(entity_id, html_url="https://a.example/new.html")],
            collection_title=first.title,
        )

        stored = await entity_store.get_entity("py", "pkg.Thing")
        assert stored is not None
        assert [link.html_url for link in stored.links] == [
            "https://a.example/new.html",
            "https://b.example/keep.html",
        ]


@pytest.mark.asyncio
async def test_replace_source_links_with_nothing_clears_them(
    factory: Factory,
) -> None:
    """A source that documents nothing any more keeps no links."""
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        source_store = factory.create_intersphinx_source_store()
        source = await source_store.add_source(
            url="https://a.example/objects.inv", title="A docs"
        )
        entity_ids = await entity_store.upsert_entities([_entity("pkg.Thing")])
        await entity_store.replace_source_links(
            source.id,
            [
                _source_link(
                    entity_ids["py", "pkg.Thing"],
                    html_url="https://a.example/old.html",
                )
            ],
            collection_title=source.title,
        )

        assert (
            await entity_store.replace_source_links(
                source.id, [], collection_title=source.title
            )
            == 0
        )

        stored = await entity_store.get_entity("py", "pkg.Thing")
        assert stored is not None
        assert stored.links == []
        # The base link row goes with the subtype row rather than being
        # orphaned by the delete.
        assert (
            await factory.db_session.execute(select(SqlLink))
        ).scalars().all() == []


@pytest.mark.asyncio
async def test_prune_keeps_entities_a_source_documents(
    factory: Factory,
) -> None:
    """An entity with a link from any source is kept."""
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        source_store = factory.create_intersphinx_source_store()
        source = await source_store.add_source(
            url="https://a.example/objects.inv", title="A docs"
        )
        entity_ids = await entity_store.upsert_entities([_entity("pkg.Thing")])
        await entity_store.replace_source_links(
            source.id,
            [
                _source_link(
                    entity_ids["py", "pkg.Thing"],
                    html_url="https://a.example/api.html#pkg.Thing",
                )
            ],
            collection_title=source.title,
        )

        assert await entity_store.prune_orphan_entities() == 0
        assert await entity_store.get_entity("py", "pkg.Thing") is not None


@pytest.mark.asyncio
async def test_prune_keeps_ancestors_of_documented_entities(
    factory: Factory,
) -> None:
    """A package no source documents survives while a descendant is
    documented, so the hierarchy above a documented object stays whole.
    """
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        source_store = factory.create_intersphinx_source_store()
        source = await source_store.add_source(
            url="https://a.example/objects.inv", title="A docs"
        )
        entity_ids = await entity_store.upsert_entities(
            [
                _entity("pkg", role="module"),
                _entity("pkg.mod", role="module", parent_name="pkg"),
                _entity("pkg.mod.Thing", parent_name="pkg.mod"),
            ]
        )
        await entity_store.replace_source_links(
            source.id,
            [
                _source_link(
                    entity_ids["py", "pkg.mod.Thing"],
                    html_url="https://a.example/api.html#pkg.mod.Thing",
                )
            ],
            collection_title=source.title,
        )

        assert await entity_store.prune_orphan_entities() == 0
        assert await entity_store.get_entity("py", "pkg") is not None
        assert await entity_store.get_entity("py", "pkg.mod") is not None


@pytest.mark.asyncio
async def test_prune_deletes_entities_nothing_documents(
    factory: Factory,
) -> None:
    """An entity with no links and no documented descendants is pruned."""
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        source_store = factory.create_intersphinx_source_store()
        source = await source_store.add_source(
            url="https://a.example/objects.inv", title="A docs"
        )
        entity_ids = await entity_store.upsert_entities(
            [
                _entity("pkg", role="module"),
                _entity("pkg.Kept", parent_name="pkg"),
                _entity("pkg.Dropped", parent_name="pkg"),
            ]
        )
        await entity_store.replace_source_links(
            source.id,
            [
                _source_link(
                    entity_ids["py", "pkg.Kept"],
                    html_url="https://a.example/api.html#pkg.Kept",
                ),
                _source_link(
                    entity_ids["py", "pkg.Dropped"],
                    html_url="https://a.example/api.html#pkg.Dropped",
                ),
            ],
            collection_title=source.title,
        )

        # The site stops documenting pkg.Dropped.
        await entity_store.replace_source_links(
            source.id,
            [
                _source_link(
                    entity_ids["py", "pkg.Kept"],
                    html_url="https://a.example/api.html#pkg.Kept",
                )
            ],
            collection_title=source.title,
        )

        assert await entity_store.prune_orphan_entities() == 1
        assert await entity_store.get_entity("py", "pkg.Dropped") is None
        assert await entity_store.get_entity("py", "pkg.Kept") is not None
        assert await entity_store.get_entity("py", "pkg") is not None


@pytest.mark.asyncio
async def test_prune_deletes_a_whole_undocumented_subtree(
    factory: Factory,
) -> None:
    """Deregistering the last source for a package prunes the package and
    everything under it.
    """
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        source_store = factory.create_intersphinx_source_store()
        source = await source_store.add_source(
            url="https://a.example/objects.inv", title="A docs"
        )
        entity_ids = await entity_store.upsert_entities(
            [
                _entity("pkg", role="module"),
                _entity("pkg.Thing", parent_name="pkg"),
            ]
        )
        await entity_store.replace_source_links(
            source.id,
            [
                _source_link(
                    entity_ids["py", "pkg.Thing"],
                    html_url="https://a.example/api.html#pkg.Thing",
                )
            ],
            collection_title=source.title,
        )

        await entity_store.replace_source_links(
            source.id, [], collection_title=source.title
        )

        assert await entity_store.prune_orphan_entities() == 2
        assert await entity_store.get_entity("py", "pkg") is None
        assert await entity_store.get_entity("py", "pkg.Thing") is None


@pytest.mark.asyncio
async def test_get_entities_returns_entities_with_links(
    factory: Factory,
) -> None:
    """A page of entities carries each one's links and the total count."""
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        source_store = factory.create_intersphinx_source_store()
        source = await source_store.add_source(
            url="https://a.example/objects.inv", title="A docs"
        )
        entity_ids = await entity_store.upsert_entities(
            [
                _entity("pkg", role="module"),
                _entity("pkg.Thing", parent_name="pkg"),
            ]
        )
        await entity_store.replace_source_links(
            source.id,
            [
                _source_link(
                    entity_ids["py", "pkg.Thing"],
                    html_url="https://a.example/api.html#pkg.Thing",
                )
            ],
            collection_title=source.title,
        )

        page = await entity_store.get_entities("py", limit=10)

        assert page.count == 2
        assert [entry.name for entry in page.entries] == ["pkg", "pkg.Thing"]
        # The package holds no page of its own, which is a real state
        # rather than missing data: it is kept alive by the class beneath.
        assert page.entries[0].links == []
        assert page.entries[1].links == [
            Link(
                html_url="https://a.example/api.html#pkg.Thing",
                type="python_api",
                title="pkg.Thing",
                collection_title="A docs",
            )
        ]


@pytest.mark.asyncio
async def test_get_entities_orders_links_by_site_title(
    factory: Factory,
) -> None:
    """A page's links carry the same site order one entity's links do.

    As in the single-entity test, site title and URL disagree about the
    order, so the assertion pins the site sort rather than the URL
    tiebreaker.
    """
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        source_store = factory.create_intersphinx_source_store()
        zebra = await source_store.add_source(
            url="https://a.example/objects.inv", title="Zebra docs"
        )
        alpha = await source_store.add_source(
            url="https://z.example/objects.inv", title="Alpha docs"
        )
        entity_ids = await entity_store.upsert_entities([_entity("pkg.Thing")])
        entity_id = entity_ids["py", "pkg.Thing"]
        for source, host in ((zebra, "a.example"), (alpha, "z.example")):
            await entity_store.replace_source_links(
                source.id,
                [
                    _source_link(
                        entity_id,
                        html_url=f"https://{host}/api.html#pkg.Thing",
                    )
                ],
                collection_title=source.title,
            )

        page = await entity_store.get_entities("py", limit=10)

        assert [link.collection_title for link in page.entries[0].links] == [
            "Alpha docs",
            "Zebra docs",
        ]


@pytest.mark.asyncio
async def test_get_entities_pages_without_dropping_or_repeating(
    factory: Factory,
) -> None:
    """Walking every page yields each entity exactly once, in name order."""
    names = [f"pkg.Thing{index:02d}" for index in range(7)]
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        await entity_store.upsert_entities(
            [_entity("pkg", role="module"), *(_entity(n) for n in names)]
        )
        expected = sorted(["pkg", *names])

        seen: list[str] = []
        cursor = None
        while True:
            page = await entity_store.get_entities(
                "py", limit=3, cursor=cursor
            )
            assert page.count == len(expected)
            seen.extend(entry.name for entry in page.entries)
            cursor = page.next_cursor
            if cursor is None:
                break

        assert seen == expected


@pytest.mark.asyncio
async def test_get_entities_pages_backwards(factory: Factory) -> None:
    """A previous cursor returns the page before it, in forward order."""
    names = [f"pkg.Thing{index:02d}" for index in range(5)]
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        await entity_store.upsert_entities([_entity(n) for n in names])

        first = await entity_store.get_entities("py", limit=2)
        assert first.next_cursor is not None
        second = await entity_store.get_entities(
            "py", limit=2, cursor=first.next_cursor
        )
        assert second.prev_cursor is not None

        back = await entity_store.get_entities(
            "py", limit=2, cursor=second.prev_cursor
        )

        assert [entry.name for entry in back.entries] == [
            entry.name for entry in first.entries
        ]


@pytest.mark.asyncio
async def test_get_entities_is_scoped_to_one_sphinx_domain(
    factory: Factory,
) -> None:
    """Another Sphinx domain's entities are neither listed nor counted."""
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        await entity_store.upsert_entities(
            [
                _entity("pkg.Thing"),
                _entity("pkg.Thing", role="label", sphinx_domain="std"),
            ]
        )

        page = await entity_store.get_entities("std", limit=10)

        assert page.count == 1
        assert [entry.sphinx_domain for entry in page.entries] == ["std"]
        assert page.entries[0].role == "label"


@pytest.mark.asyncio
async def test_get_children_returns_direct_children_with_links(
    factory: Factory,
) -> None:
    """A module's page lists its own members, each with its links."""
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        source_store = factory.create_intersphinx_source_store()
        source = await source_store.add_source(
            url="https://a.example/objects.inv", title="A docs"
        )
        entity_ids = await entity_store.upsert_entities(
            [
                _entity("pkg", role="module"),
                _entity("pkg.mod", role="module", parent_name="pkg"),
                _entity("pkg.mod.Thing", parent_name="pkg.mod"),
            ]
        )
        await entity_store.replace_source_links(
            source.id,
            [
                _source_link(
                    entity_ids["py", "pkg.mod"],
                    html_url="https://a.example/api.html#pkg.mod",
                    title="pkg.mod",
                )
            ],
            collection_title=source.title,
        )

        page = await entity_store.get_children("py", "pkg", limit=10)

        assert page is not None
        assert page.count == 1
        assert [entry.name for entry in page.entries] == ["pkg.mod"]
        assert page.entries[0].links == [
            Link(
                html_url="https://a.example/api.html#pkg.mod",
                type="python_api",
                title="pkg.mod",
                collection_title="A docs",
            )
        ]


@pytest.mark.asyncio
async def test_get_children_of_unknown_parent_is_none(
    factory: Factory,
) -> None:
    """A name no entity answers to has no children page at all.

    None rather than an empty page, because the two answer different
    questions: this parent does not exist, versus this parent contains
    nothing.
    """
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        await entity_store.upsert_entities([_entity("pkg", role="module")])

        assert await entity_store.get_children("py", "nothing.here") is None


@pytest.mark.asyncio
async def test_get_children_of_a_leaf_is_an_empty_page(
    factory: Factory,
) -> None:
    """An entity that contains nothing has an empty page of children."""
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        await entity_store.upsert_entities(
            [
                _entity("pkg", role="module"),
                _entity("pkg.Thing", parent_name="pkg"),
            ]
        )

        page = await entity_store.get_children("py", "pkg.Thing", limit=10)

        assert page is not None
        assert page.count == 0
        assert page.entries == []


@pytest.mark.asyncio
async def test_get_children_is_scoped_to_one_parent(factory: Factory) -> None:
    """Another module's members are neither listed nor counted."""
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        await entity_store.upsert_entities(
            [
                _entity("pkg.a", role="module"),
                _entity("pkg.b", role="module"),
                _entity("pkg.a.Thing", parent_name="pkg.a"),
                _entity("pkg.b.Other", parent_name="pkg.b"),
            ]
        )

        page = await entity_store.get_children("py", "pkg.a", limit=10)

        assert page is not None
        assert page.count == 1
        assert [entry.name for entry in page.entries] == ["pkg.a.Thing"]


@pytest.mark.asyncio
async def test_get_children_pages_without_dropping_or_repeating(
    factory: Factory,
) -> None:
    """Walking every page of children yields each one exactly once."""
    names = [f"pkg.Thing{index:02d}" for index in range(7)]
    async with factory.db_session.begin():
        entity_store = factory.create_intersphinx_entity_store()
        await entity_store.upsert_entities(
            [
                _entity("pkg", role="module"),
                *(_entity(name, parent_name="pkg") for name in names),
            ]
        )

        seen: list[str] = []
        cursor = None
        while True:
            page = await entity_store.get_children(
                "py", "pkg", limit=3, cursor=cursor
            )
            assert page is not None
            assert page.count == len(names)
            seen.extend(entry.name for entry in page.entries)
            cursor = page.next_cursor
            if cursor is None:
                break

        assert seen == sorted(names)
