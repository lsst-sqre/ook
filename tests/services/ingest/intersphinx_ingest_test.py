"""Tests for the IntersphinxIngestService."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
import sphobjinv
import structlog
from httpx import Response
from safir.database import create_async_session, create_database_engine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ook.config import config
from ook.dbschema.intersphinxentities import SqlIntersphinxEntity
from ook.dbschema.links import SqlIntersphinxLink
from ook.domain.intersphinx import (
    IntersphinxInventory,
    InventoryCacheStatus,
    InventoryFetchStatus,
)
from ook.domain.intersphinxentities import (
    SPHINX_DOMAIN_HIERARCHIES,
    IntersphinxSourceLink,
)
from ook.domain.intersphinxsources import IntersphinxSource, SourceIngestStatus
from ook.domain.links import Link
from ook.exceptions import NotFoundError
from ook.factory import Factory
from ook.services.ingest.intersphinx import SPHINX_DOMAIN_LINK_TYPES
from ook.storage.intersphinxentitystore import IntersphinxEntityStore
from ook.storage.intersphinxsourcestore import IntersphinxSourceStore

INVENTORY_URL = "https://a.example/en/latest/objects.inv"
"""The inventory URL of the documentation site most tests register."""

OTHER_INVENTORY_URL = "https://b.example/objects.inv"
"""A second site's inventory URL, for the multi-source tests."""


def _inventory(objects: Sequence[tuple[str, str, str]]) -> bytes:
    """Build an ``objects.inv`` payload from ``(name, role, uri)`` triples.

    Written with sphobjinv rather than checked in as bytes, because these
    tests are about what ingest does with an inventory's *contents*, and a
    fixture file cannot have one object removed from it mid-test.
    """
    inventory = sphobjinv.Inventory()
    inventory.project = "Example"
    inventory.version = "1.0"
    for name, role, uri in objects:
        inventory.objects.append(
            sphobjinv.DataObjStr(
                name=name,
                domain="py",
                role=role,
                priority="1",
                uri=uri,
                dispname="-",
            )
        )
    return sphobjinv.compress(inventory.data_file())


PACKAGE_INVENTORY = _inventory(
    [
        ("pkg", "module", "api.html#module-pkg"),
        ("pkg.mod", "module", "api.html#module-pkg.mod"),
        ("pkg.mod.Thing", "class", "api.html#pkg.mod.Thing"),
        ("pkg.Standalone", "class", "api.html#pkg.Standalone"),
    ]
)
"""An inventory documenting a package, a module in it, and two classes."""


NARROWED_INVENTORY = _inventory(
    [
        ("pkg", "module", "api.html#module-pkg"),
        ("pkg.mod.Thing", "class", "api.html#pkg.mod.Thing"),
    ]
)
"""`PACKAGE_INVENTORY` after the site stopped publishing two of its pages.

``pkg.Standalone`` is gone outright and ``pkg.mod`` keeps only the class
inside it, which is the ordinary way a site drops a module's own page while
still documenting its contents.
"""


A_ONLY_INVENTORY = _inventory([("apkg", "module", "api.html#module-apkg")])
"""One site's inventory before it also documented the shared object."""


A_AND_SHARED_INVENTORY = _inventory(
    [
        ("apkg", "module", "api.html#module-apkg"),
        ("shared", "module", "api.html#module-shared"),
    ]
)
"""`A_ONLY_INVENTORY` after the site started documenting ``shared`` too.

The second site documents ``shared`` already, so ingesting this is what
makes ``shared`` an object two sites link -- the state a prune racing that
ingest can destroy.
"""


SHARED_INVENTORY = _inventory([("shared", "module", "api.html#module-shared")])
"""A second site's inventory, documenting only the shared object."""


B_ONLY_INVENTORY = _inventory([("bpkg", "module", "api.html#module-bpkg")])
"""`SHARED_INVENTORY` after the second site dropped the shared object.

Every name in these four inventories is top level, so a recompute of
containment rewrites no row: what a test built on them observes is the
prune alone.
"""


async def _stored_python_domain(
    factory: Factory,
) -> list[tuple[str, str | None, tuple[str, ...]]]:
    """Read the whole stored ``py`` domain as name, parent, and link URLs.

    Everything an ingest is answerable for and nothing that depends on when
    it ran, so two ingest histories that agree about what the sites document
    compare equal.
    """
    async with factory.db_session.begin():
        page = await factory.create_intersphinx_entity_store().get_entities(
            "py"
        )
    return [
        (
            entry.name,
            entry.parent_name,
            tuple(sorted(link.html_url for link in entry.links)),
        )
        for entry in page.entries
    ]


async def _delete_source(factory: Factory, source_id: int) -> None:
    """Deregister a source through the service the API calls."""
    async with factory.db_session.begin():
        service = factory.create_intersphinx_source_service()
        assert await service.delete_source(source_id) is True


def _serve_inventory(
    respx_mock: respx.Router, url: str, content: bytes
) -> respx.Route:
    """Serve an inventory payload from a mocked origin."""
    return respx_mock.get(url).mock(
        return_value=Response(
            200,
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
    )


async def _register_source(
    factory: Factory, *, url: str, title: str, enabled: bool = True
) -> int:
    """Register a documentation source and return its ID."""
    async with factory.db_session.begin():
        source = await factory.create_intersphinx_source_store().add_source(
            url=url, title=title, enabled=enabled
        )
    return source.id


async def _get_source(factory: Factory, source_id: int) -> IntersphinxSource:
    """Read a registered source back as the ingest path receives it."""
    async with factory.db_session.begin():
        source = await factory.create_intersphinx_source_store().get_source(
            source_id
        )
    assert source is not None
    return source


async def _store_parent(
    factory: Factory, name: str, *, parent_name: str
) -> None:
    """Write one entity's stored parent directly, bypassing the derivation.

    What a release with a different containment rule would have left in the
    table, which is the one way to have a row disagree with what the links
    say without touching the links themselves.
    """
    async with factory.db_session.begin():
        parent_id = (
            await factory.db_session.execute(
                select(SqlIntersphinxEntity.id).where(
                    SqlIntersphinxEntity.sphinx_domain == "py",
                    SqlIntersphinxEntity.name == parent_name,
                )
            )
        ).scalar_one()
        await factory.db_session.execute(
            update(SqlIntersphinxEntity)
            .where(
                SqlIntersphinxEntity.sphinx_domain == "py",
                SqlIntersphinxEntity.name == name,
            )
            .values(parent_id=parent_id)
        )


async def _link_row_ids(factory: Factory) -> list[int]:
    """Read the primary key of every stored intersphinx link row.

    A replace is a delete followed by inserts, so these IDs are what tells
    "the links were left alone" apart from "the links were rebuilt to look
    exactly as they did".
    """
    async with factory.db_session.begin():
        rows = await factory.db_session.execute(
            select(SqlIntersphinxLink.id).order_by(SqlIntersphinxLink.id)
        )
        return list(rows.scalars().all())


async def _entity_row_ids(factory: Factory) -> list[int]:
    """Read the primary key of every stored entity row.

    Entities are upserted rather than replaced, so these survive a
    re-ingest either way -- what they catch is an ingest that ran the upsert
    at all, since a name whose row is re-created gets a new ID.
    """
    async with factory.db_session.begin():
        rows = await factory.db_session.execute(
            select(SqlIntersphinxEntity.id).order_by(SqlIntersphinxEntity.id)
        )
        return list(rows.scalars().all())


BLOCKED_GRACE = 0.5
"""How long an ingest a test expects to be blocked is given to prove it.

An unblocked ingest of these four-object inventories finishes in
milliseconds, so half a second is generous enough to be reliable and short
enough to keep the test cheap.
"""

UNBLOCKED_TIMEOUT = 30.0
"""How long an ingest a test expects to *finish* is waited on.

Only a bound on a hung test; an unblocked ingest finishes at once.
"""


@asynccontextmanager
async def _second_session() -> AsyncIterator[AsyncSession]:
    """Open a second database session, on a connection of its own.

    Lock contention is only observable between two connections, and the
    ``factory`` fixture hands out one. This opens the other, on its own
    engine so neither session can be starved of the other's pool.
    """
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    session = await create_async_session(engine)
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


@asynccontextmanager
async def _second_factory(engine: AsyncEngine) -> AsyncIterator[Factory]:
    """Build a second factory, and so a second ingest service and session.

    Wired the way the application wires one rather than by hand, so the two
    ingests a concurrency test races are the same object the CronJob and the
    endpoint run.
    """
    async with Factory.create_standalone(
        logger=structlog.get_logger("test"), engine=engine
    ) as factory:
        yield factory


async def _wait_until(
    condition: Callable[[], bool], *, timeout: float = UNBLOCKED_TIMEOUT
) -> None:
    """Wait for a condition another task is expected to bring about."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise AssertionError("The awaited condition never became true.")
        await asyncio.sleep(0.01)


def _delete_source_while_serving(
    respx_mock: respx.Router,
    url: str,
    content: bytes,
    *,
    session: AsyncSession,
    source_id: int,
) -> respx.Route:
    """Serve an inventory, deleting its registration first.

    The delete is committed on a second session, from inside the origin's
    response, which puts it exactly where the race this guards against puts
    it: after the ingest has committed to fetching the inventory and before
    it can lock the registration those links belong to.
    """

    async def handler(request: httpx.Request) -> Response:
        store = IntersphinxSourceStore(
            session=session, logger=structlog.get_logger("test")
        )
        async with session.begin():
            await store.delete_source(source_id)
        return Response(
            200,
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )

    return respx_mock.get(url).mock(side_effect=handler)


def _repoint_source_while_serving(
    respx_mock: respx.Router,
    url: str,
    content: bytes,
    *,
    session: AsyncSession,
    source_id: int,
    new_url: str,
) -> respx.Route:
    """Serve an inventory, repointing its registration at a new URL first.

    `_delete_source_while_serving` for the other thing an operator can do
    to a registration in this window. The repoint is committed on a second
    session from inside the origin's response, so the ingest has already
    committed to fetching *url* and has not yet locked the registration
    those links would belong to.
    """

    async def handler(request: httpx.Request) -> Response:
        store = IntersphinxSourceStore(
            session=session, logger=structlog.get_logger("test")
        )
        async with session.begin():
            await store.update_source(source_id, url=new_url)
        return Response(
            200,
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )

    return respx_mock.get(url).mock(side_effect=handler)


MISSING_ENTITY_ID = -1
"""An entity ID no row can ever hold, for provoking a real write failure."""


def _link_to_a_missing_entity(
    monkeypatch: pytest.MonkeyPatch, *, source_id: int
) -> None:
    """Make one site's link write violate the entity foreign key.

    The database's own refusal rather than a raised stand-in, so what the
    ingest has to recover from is a genuinely aborted transaction -- which
    is the part of losing this race that is easy to get wrong.
    """
    replace_source_links = IntersphinxEntityStore.replace_source_links

    async def broken(
        self: IntersphinxEntityStore,
        replaced_source_id: int,
        links: Sequence[IntersphinxSourceLink],
        *,
        collection_title: str | None,
    ) -> int:
        if replaced_source_id == source_id:
            links = [
                replace(link, entity_id=MISSING_ENTITY_ID) for link in links
            ]
        return await replace_source_links(
            self, replaced_source_id, links, collection_title=collection_title
        )

    monkeypatch.setattr(IntersphinxEntityStore, "replace_source_links", broken)


def _pause_after_link_replace(
    monkeypatch: pytest.MonkeyPatch, *, source_id: int
) -> tuple[asyncio.Event, asyncio.Event]:
    """Hold one source's ingest open between writing its links and committing.

    The window every race in this section is about. An ingest that has
    replaced its links but not committed them is invisible to any other
    transaction, so a convergence running beside it judges the entity graph
    on links that are about to exist -- and that is the only moment at which
    a prune can decide an entity nobody documents.

    Patched on the store class rather than on one instance because the
    ingest service builds its own store; the source ID is what keeps the
    pause to the one ingest a test means to hold.

    Returns
    -------
    tuple of asyncio.Event
        The event set once the links are written, and the event the test
        sets to let the ingest commit.
    """
    written = asyncio.Event()
    release = asyncio.Event()
    replace_source_links = IntersphinxEntityStore.replace_source_links

    async def paused(
        self: IntersphinxEntityStore,
        replaced_source_id: int,
        links: Sequence[IntersphinxSourceLink],
        *,
        collection_title: str | None,
    ) -> int:
        count = await replace_source_links(
            self, replaced_source_id, links, collection_title=collection_title
        )
        if replaced_source_id == source_id:
            written.set()
            await release.wait()
        return count

    monkeypatch.setattr(IntersphinxEntityStore, "replace_source_links", paused)
    return written, release


async def _expire_cached_inventory(factory: Factory, url: str) -> None:
    """Drop a cached inventory's content so the next ingest refetches it.

    Ingest pulls every inventory through the DM-55387 cache, which serves a
    stored copy without contacting the origin, so a test that wants the
    origin consulted again has to take the stored copy away first. The row
    is left as an expired negative-cache entry -- no content, a failure
    status, and a fetch time older than the negative TTL -- which is the one
    shape the cache treats as a cold miss.
    """
    async with factory.db_session.begin():
        await factory.create_intersphinx_inventory_store().upsert_inventory(
            IntersphinxInventory(
                url=url,
                content=None,
                content_type=None,
                etag=None,
                last_modified=None,
                date_fetched=datetime.now(tz=UTC) - timedelta(days=1),
                date_requested=datetime.now(tz=UTC) - timedelta(days=1),
                last_fetch_status=InventoryFetchStatus.failure,
                last_fetch_error="expired by the test",
                date_refresh_failed=None,
            )
        )


def test_every_modelled_domain_has_a_link_type() -> None:
    """Every Sphinx domain Ook stores can be turned into a link.

    The two mappings answer halves of the same question -- which domains
    Ook models, and what kind of documentation each one's links are -- so a
    domain added to one and not the other would fail an ingest at the
    moment it first met an object of that domain.
    """
    assert set(SPHINX_DOMAIN_LINK_TYPES) == set(SPHINX_DOMAIN_HIERARCHIES)


@pytest.mark.asyncio
async def test_ingest_populates_entities_and_links(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """An ingest run stores each object with its hierarchy and its link."""
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )

    summary = (
        await factory.create_intersphinx_ingest_service().ingest_sources()
    )

    assert summary.failed == 0
    assert summary.succeeded == 1
    assert summary.results[0].source_id == source_id
    assert summary.results[0].link_count == 4

    entity_store = factory.create_intersphinx_entity_store()
    thing = await entity_store.get_entity("py", "pkg.mod.Thing")
    assert thing is not None
    assert thing.role == "class"
    # The hierarchy comes from the dotted name, resolved against what this
    # inventory documents.
    assert thing.parent_name == "pkg.mod"
    # The inventory's URI is relative to the directory holding it, so the
    # link is resolved against the inventory URL rather than the site root.
    assert thing.links == [
        Link(
            html_url="https://a.example/en/latest/api.html#pkg.mod.Thing",
            type="python_api",
            title="pkg.mod.Thing",
            collection_title="A docs",
        )
    ]


@pytest.mark.asyncio
async def test_ingest_records_success_on_the_registry_row(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A successful ingest stamps its outcome on the source's registration."""
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )

    await factory.create_intersphinx_ingest_service().ingest_sources()

    source = await factory.create_intersphinx_source_store().get_source(
        source_id
    )
    assert source is not None
    assert source.last_status is SourceIngestStatus.success
    assert source.last_error is None
    assert source.date_ingested is not None


@pytest.mark.asyncio
async def test_a_full_reingest_is_idempotent(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """Replacing a site's links a second time leaves one link per object.

    A sweep over a site that has not republished recognizes its inventory
    and never reaches the replace, so this drives it there deliberately --
    by retitling the registration, which invalidates the stored links
    without touching the inventory behind them. What runs is the same
    replace a republished site, a recovered failure, and a retitle all end
    up running.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    service = factory.create_intersphinx_ingest_service()
    first = await service.ingest_sources()
    async with factory.db_session.begin():
        await factory.create_intersphinx_source_store().update_source(
            source_id, title="A docs, renamed"
        )

    second = await service.ingest_sources()

    assert first.link_count == second.link_count == 4
    assert second.pruned_count == 0
    entity_store = factory.create_intersphinx_entity_store()
    thing = await entity_store.get_entity("py", "pkg.mod.Thing")
    assert thing is not None
    assert len(thing.links) == 1


@pytest.mark.asyncio
async def test_a_reingest_converges_on_what_the_site_documents_now(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """Ingesting A then B leaves what ingesting B alone would leave.

    The property the derived hierarchy exists for. Stored containment is a
    function of the links that exist, so nothing about the site's earlier
    inventory survives into the answer -- not the module whose page it
    dropped, and not the nesting that module used to provide.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()

    await _expire_cached_inventory(factory, INVENTORY_URL)
    _serve_inventory(respx_mock, INVENTORY_URL, NARROWED_INVENTORY)
    await service.ingest_sources()
    after_history = await _stored_python_domain(factory)

    # Start the site over: deregistering it empties the domain, and the
    # re-registration ingests the narrowed inventory with no past.
    await _delete_source(factory, source_id)
    assert await _stored_python_domain(factory) == []
    await _register_source(factory, url=INVENTORY_URL, title="A docs")

    await service.ingest_sources()

    assert after_history == await _stored_python_domain(factory)


@pytest.mark.asyncio
async def test_a_dropped_module_unnests_the_classes_it_held(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A module the only site documenting it drops takes no classes with it.

    The class is still documented, so it stays -- as a top-level object,
    because the module that contained it is not documented anywhere any
    more and containment says a documented object holds this one.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()

    await _expire_cached_inventory(factory, INVENTORY_URL)
    _serve_inventory(respx_mock, INVENTORY_URL, NARROWED_INVENTORY)

    summary = await service.ingest_sources()

    assert summary.pruned_count == 2
    entity_store = factory.create_intersphinx_entity_store()
    assert await entity_store.get_entity("py", "pkg.Standalone") is None
    assert await entity_store.get_entity("py", "pkg.mod") is None
    thing = await entity_store.get_entity("py", "pkg.mod.Thing")
    assert thing is not None
    assert thing.parent_name is None


@pytest.mark.asyncio
async def test_every_stored_entity_has_a_link(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """No ingest leaves behind an object no site documents.

    What the API's 404-or-links promise rests on: there is no third state
    for an endpoint to answer with.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, NARROWED_INVENTORY)
    await _register_source(factory, url=INVENTORY_URL, title="A docs")

    await factory.create_intersphinx_ingest_service().ingest_sources()

    stored = await _stored_python_domain(factory)
    assert stored
    assert all(html_urls for _, _, html_urls in stored)


@pytest.mark.asyncio
async def test_a_class_nests_under_a_module_another_site_documents(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """Containment crosses sites, because entities are shared by name.

    Neither inventory documents both halves, so no single ingest could
    resolve this parent: the module's page comes from one site and the
    class's from the other.
    """
    _serve_inventory(
        respx_mock,
        INVENTORY_URL,
        _inventory([("shared.mod", "module", "api.html#module-shared.mod")]),
    )
    _serve_inventory(
        respx_mock,
        OTHER_INVENTORY_URL,
        _inventory(
            [("shared.mod.Thing", "class", "api.html#shared.mod.Thing")]
        ),
    )
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    await _register_source(factory, url=OTHER_INVENTORY_URL, title="B docs")

    await factory.create_intersphinx_ingest_service().ingest_sources()

    thing = await factory.create_intersphinx_entity_store().get_entity(
        "py", "shared.mod.Thing"
    )
    assert thing is not None
    assert thing.parent_name == "shared.mod"


@pytest.mark.asyncio
async def test_deleting_a_site_unnests_another_sites_classes(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """Deregistering the site that documented a module unnests it at once.

    Not at the next scheduled ingest: until the module's page is withdrawn
    the Links API would keep serving a hierarchy propped up by a site Ook
    is no longer ingesting.
    """
    _serve_inventory(
        respx_mock,
        INVENTORY_URL,
        _inventory([("shared.mod", "module", "api.html#module-shared.mod")]),
    )
    _serve_inventory(
        respx_mock,
        OTHER_INVENTORY_URL,
        _inventory(
            [("shared.mod.Thing", "class", "api.html#shared.mod.Thing")]
        ),
    )
    module_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    await _register_source(factory, url=OTHER_INVENTORY_URL, title="B docs")
    await factory.create_intersphinx_ingest_service().ingest_sources()

    await _delete_source(factory, module_id)

    entity_store = factory.create_intersphinx_entity_store()
    assert await entity_store.get_entity("py", "shared.mod") is None
    thing = await entity_store.get_entity("py", "shared.mod.Thing")
    assert thing is not None
    assert thing.parent_name is None


@pytest.mark.asyncio
async def test_a_site_that_stops_documenting_a_module_unnests_it(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """The same unnesting when the site stays registered but stops publishing.

    A deregistration and a republished inventory reach the stored state by
    different paths; the state itself is decided by the same rule.
    """
    _serve_inventory(
        respx_mock,
        INVENTORY_URL,
        _inventory([("shared.mod", "module", "api.html#module-shared.mod")]),
    )
    _serve_inventory(
        respx_mock,
        OTHER_INVENTORY_URL,
        _inventory(
            [("shared.mod.Thing", "class", "api.html#shared.mod.Thing")]
        ),
    )
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    await _register_source(factory, url=OTHER_INVENTORY_URL, title="B docs")
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()

    await _expire_cached_inventory(factory, INVENTORY_URL)
    _serve_inventory(
        respx_mock,
        INVENTORY_URL,
        _inventory([("elsewhere", "module", "api.html#module-elsewhere")]),
    )

    await service.ingest_sources()

    entity_store = factory.create_intersphinx_entity_store()
    assert await entity_store.get_entity("py", "shared.mod") is None
    thing = await entity_store.get_entity("py", "shared.mod.Thing")
    assert thing is not None
    assert thing.parent_name is None


@pytest.mark.asyncio
async def test_deleting_the_last_source_empties_the_domain(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """Nothing is documented once the last registration goes."""
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    await factory.create_intersphinx_ingest_service().ingest_sources()

    await _delete_source(factory, source_id)

    assert await _stored_python_domain(factory) == []


@pytest.mark.asyncio
async def test_reingest_skips_an_unchanged_inventory(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A second ingest of the same bytes reports that it did nothing.

    The common case for a scheduled sweep is a site that has not
    republished, and rewriting its links into an identical copy of
    themselves is work with no result -- so the run says so instead, with
    the counts of what it wrote, which is nothing.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()

    summary = await service.ingest_sources()

    result = summary.results[0]
    assert result.unchanged is True
    assert result.status is SourceIngestStatus.success
    assert result.entity_count == 0
    assert result.link_count == 0
    assert result.pruned_count == 0
    assert summary.unchanged_count == 1


@pytest.mark.asyncio
async def test_a_skipped_source_keeps_the_rows_it_already_had(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """Skipping an unchanged inventory writes no entity or link rows."""
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()
    link_ids = await _link_row_ids(factory)
    entity_ids = await _entity_row_ids(factory)

    await service.ingest_sources()

    assert await _link_row_ids(factory) == link_ids
    assert await _entity_row_ids(factory) == entity_ids


@pytest.mark.asyncio
async def test_a_skipped_source_is_still_stamped_as_ingested(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A skipped source was visited, so its registration says when.

    ``date_ingested`` answers "when did Ook last check this site?", which a
    run that recognized the inventory answered as much as one that replaced
    every link from it.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()
    first = (await _get_source(factory, source_id)).date_ingested

    await service.ingest_sources()

    source = await _get_source(factory, source_id)
    assert first is not None
    assert source.date_ingested is not None
    assert source.date_ingested > first
    assert source.last_status is SourceIngestStatus.success


@pytest.mark.asyncio
async def test_a_changed_inventory_is_reingested(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A site that republished is read in full, not recognized."""
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()

    await _expire_cached_inventory(factory, INVENTORY_URL)
    _serve_inventory(
        respx_mock,
        INVENTORY_URL,
        _inventory(
            [
                ("pkg", "module", "api.html#module-pkg"),
                ("pkg.Added", "class", "api.html#pkg.Added"),
            ]
        ),
    )

    summary = await service.ingest_sources()

    assert summary.results[0].unchanged is False
    assert summary.unchanged_count == 0
    entity_store = factory.create_intersphinx_entity_store()
    assert await entity_store.get_entity("py", "pkg.Added") is not None


@pytest.mark.asyncio
async def test_a_failed_ingest_forces_the_next_one_to_do_the_work(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A source recovering from a failure is re-ingested, not recognized.

    The digest describes the links the last *success* wrote, and a failure
    does not disturb them -- but the run after a failure is the one an
    operator is watching, and skipping it on a digest match would leave the
    site's recovery unproven.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()

    await _expire_cached_inventory(factory, INVENTORY_URL)
    respx_mock.get(INVENTORY_URL).mock(return_value=Response(503))
    assert (await service.ingest_sources()).failed == 1

    await _expire_cached_inventory(factory, INVENTORY_URL)
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)

    summary = await service.ingest_sources()

    assert summary.succeeded == 1
    assert summary.results[0].unchanged is False
    assert summary.results[0].link_count == 4


@pytest.mark.asyncio
async def test_a_retitled_source_is_reingested(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """Renaming a site rebuilds its links even though it did not republish.

    Every link carries the registration's title as its
    ``collection_title``, so the stored links stop describing the site the
    moment it is retitled.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()

    async with factory.db_session.begin():
        await factory.create_intersphinx_source_store().update_source(
            source_id, title="A better docs"
        )

    summary = await service.ingest_sources()

    assert summary.results[0].unchanged is False
    thing = await factory.create_intersphinx_entity_store().get_entity(
        "py", "pkg.mod.Thing"
    )
    assert thing is not None
    assert [link.collection_title for link in thing.links] == ["A better docs"]


@pytest.mark.asyncio
async def test_a_sweep_of_skipped_sources_prunes_an_orphan_the_store_left(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """The sweep's own pass collects an entity no source's links keep alive.

    A lower-layer contract test, and deliberately so: the source *service*
    converges a deletion inline and under the entity graph's lock, so an
    orphaned entity is not a state the application produces any more. The
    *store*'s ``delete_source`` leaves exactly that state by contract, which
    makes driving it directly the only way to reach it -- and what this pins
    is that a run which replaces nothing still collects such an entity,
    however it came to be there.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    _serve_inventory(
        respx_mock,
        OTHER_INVENTORY_URL,
        _inventory([("other.Thing", "class", "api.html#other.Thing")]),
    )
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    other_id = await _register_source(
        factory, url=OTHER_INVENTORY_URL, title="B docs"
    )
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()
    async with factory.db_session.begin():
        await factory.create_intersphinx_source_store().delete_source(other_id)

    summary = await service.ingest_sources()

    assert summary.unchanged_count == 1
    assert summary.pruned_count == 1
    entity_store = factory.create_intersphinx_entity_store()
    assert await entity_store.get_entity("py", "other.Thing") is None
    assert await entity_store.get_entity("py", "pkg.mod.Thing") is not None


@pytest.mark.asyncio
async def test_a_sweep_of_skipped_sources_recomputes_stale_containment(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """The sweep's own pass rewrites containment no source's ingest would.

    Containment is derived, so a release that changes the derivation changes
    what every stored row should say without any site publishing anything
    new. Such a release ships with no migration because the next run
    recomputes what it finds -- and a settled fleet, whose sites all
    recognize their inventories, gives no source's ingest the chance. This
    pass is what makes that decision true, and it is the half of the pass
    ``sweep_pruned_count`` does not count.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()
    # The shape a previous release's rule could have written: a class filed
    # under a name that is not its own dotted parent.
    await _store_parent(factory, "pkg.Standalone", parent_name="pkg.mod")

    summary = await service.ingest_sources()

    assert summary.unchanged_count == 1
    assert summary.pruned_count == 0
    standalone = await factory.create_intersphinx_entity_store().get_entity(
        "py", "pkg.Standalone"
    )
    assert standalone is not None
    assert standalone.parent_name == "pkg"


@pytest.mark.asyncio
async def test_ingest_source_url_skips_an_unchanged_inventory(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """Naming one source revalidates it, then still recognizes its bytes.

    The manual trigger buys a fresh read of the site; what it does not buy
    is rewriting links the fresh read proved identical.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()

    result = await service.ingest_source_url(INVENTORY_URL)

    assert result.unchanged is True
    assert result.link_count == 0


@pytest.mark.asyncio
async def test_a_failing_source_is_recorded_and_the_run_continues(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A source whose fetch fails keeps its links and does not stop the run."""
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    _serve_inventory(
        respx_mock,
        OTHER_INVENTORY_URL,
        _inventory([("other.Thing", "class", "api.html#other.Thing")]),
    )
    failing_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()

    # The first site's origin starts failing, with no cached copy to fall
    # back on.
    await _expire_cached_inventory(factory, INVENTORY_URL)
    respx_mock.get(INVENTORY_URL).mock(return_value=Response(503))
    await _register_source(factory, url=OTHER_INVENTORY_URL, title="B docs")

    summary = await service.ingest_sources()

    assert summary.failed == 1
    assert summary.succeeded == 1
    failure = next(
        result for result in summary.results if result.source_id == failing_id
    )
    assert failure.status is SourceIngestStatus.failure
    assert failure.error is not None

    # The failure is stamped on the registry row, and the previous run's
    # links are still served.
    source = await factory.create_intersphinx_source_store().get_source(
        failing_id
    )
    assert source is not None
    assert source.last_status is SourceIngestStatus.failure
    assert source.last_error is not None
    entity_store = factory.create_intersphinx_entity_store()
    thing = await entity_store.get_entity("py", "pkg.mod.Thing")
    assert thing is not None
    assert len(thing.links) == 1
    # The healthy site was ingested in the same run.
    other = await entity_store.get_entity("py", "other.Thing")
    assert other is not None
    assert len(other.links) == 1


@pytest.mark.asyncio
async def test_a_disabled_source_is_skipped(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A sweep visits only the sources an operator enabled."""
    route = _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    await _register_source(
        factory, url=INVENTORY_URL, title="A docs", enabled=False
    )

    summary = (
        await factory.create_intersphinx_ingest_service().ingest_sources()
    )

    assert summary.results == []
    assert route.call_count == 0


@pytest.mark.asyncio
async def test_ingest_source_url_ingests_only_that_source(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """Naming one source's URL leaves every other source untouched."""
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    other_route = _serve_inventory(
        respx_mock,
        OTHER_INVENTORY_URL,
        _inventory([("other.Thing", "class", "api.html#other.Thing")]),
    )
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    await _register_source(factory, url=OTHER_INVENTORY_URL, title="B docs")

    service = factory.create_intersphinx_ingest_service()
    result = await service.ingest_source_url(INVENTORY_URL)

    assert result.status is SourceIngestStatus.success
    assert other_route.call_count == 0
    entity_store = factory.create_intersphinx_entity_store()
    assert await entity_store.get_entity("py", "pkg.mod.Thing") is not None
    assert await entity_store.get_entity("py", "other.Thing") is None


@pytest.mark.asyncio
async def test_ingest_source_url_ingests_a_disabled_source(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """Naming a source is the more specific instruction than its flag.

    ``enabled`` says whether sweeps visit a site; an operator who names one
    is not running a sweep, and this is the only way to try a registration
    out before turning it on.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    await _register_source(
        factory, url=INVENTORY_URL, title="A docs", enabled=False
    )

    service = factory.create_intersphinx_ingest_service()
    result = await service.ingest_source_url(INVENTORY_URL)

    assert result.status is SourceIngestStatus.success


@pytest.mark.asyncio
async def test_ingest_source_url_rejects_an_unregistered_url(
    factory: Factory,
) -> None:
    """An inventory URL nobody registered is not something to ingest."""
    service = factory.create_intersphinx_ingest_service()

    with pytest.raises(NotFoundError):
        await service.ingest_source_url(INVENTORY_URL)


@pytest.mark.asyncio
async def test_an_unparseable_inventory_is_a_recorded_failure(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """Bytes that are not an inventory fail the source, not the run."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=b"not an objects.inv at all")
    )
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )

    summary = (
        await factory.create_intersphinx_ingest_service().ingest_sources()
    )

    assert summary.failed == 1
    source = await factory.create_intersphinx_source_store().get_source(
        source_id
    )
    assert source is not None
    assert source.last_status is SourceIngestStatus.failure
    assert source.last_error is not None


async def _age_cached_inventory(factory: Factory, url: str) -> None:
    """Age a cached inventory past the freshness TTL, content intact.

    What a site's cached inventory looks like to the next day's ingest run:
    still perfectly servable, but old enough that ingest owes it a
    conditional GET before parsing it. `_expire_cached_inventory`'s
    counterpart, which takes the content away entirely.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        cached = await store.get_inventory(url)
        assert cached is not None
        await store.upsert_inventory(
            replace(
                cached, date_fetched=datetime.now(tz=UTC) - timedelta(days=1)
            )
        )


@pytest.mark.asyncio
async def test_a_sweep_revalidates_a_stale_inventory(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A sweep revalidates a cached inventory that has aged out.

    Ingest owns the freshness of its own sources rather than depending on
    the cache-warming refresh job having run first.
    """
    route = _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()
    await _age_cached_inventory(factory, INVENTORY_URL)

    summary = await service.ingest_sources()

    assert route.call_count == 2
    assert summary.results[0].cache_status is InventoryCacheStatus.hit


@pytest.mark.asyncio
async def test_a_sweep_leaves_a_fresh_inventory_alone(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A sweep costs no upstream request for an inventory inside the TTL."""
    route = _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()

    summary = await service.ingest_sources()

    assert route.call_count == 1
    assert summary.results[0].cache_status is InventoryCacheStatus.hit


@pytest.mark.asyncio
async def test_ingest_source_url_revalidates_a_fresh_inventory(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """Naming one source revalidates it however fresh the cached copy is.

    This is the try-it-out path an operator reaches for after a site
    republishes, so it parses what the site publishes now.
    """
    route = _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()

    result = await service.ingest_source_url(INVENTORY_URL)

    assert route.call_count == 2
    assert result.cache_status is InventoryCacheStatus.hit


@pytest.mark.asyncio
async def test_a_source_ingested_from_an_unrevalidated_copy_says_so(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A site whose origin is down is ingested from the copy Ook holds.

    The ingest succeeds -- there are links to write -- but the result
    reports that the inventory could not be revalidated, which is the only
    way an operator can tell the run apart from one that read the site.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()
    await _age_cached_inventory(factory, INVENTORY_URL)
    respx_mock.get(INVENTORY_URL).mock(return_value=Response(503))

    summary = await service.ingest_sources()

    assert summary.failed == 0
    assert summary.succeeded == 1
    assert summary.stale_count == 1
    assert summary.results[0].cache_status is InventoryCacheStatus.stale


@pytest.mark.asyncio
async def test_a_failed_source_reports_no_cache_status(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A source served no inventory at all has no freshness to report."""
    respx_mock.get(INVENTORY_URL).mock(return_value=Response(503))
    await _register_source(factory, url=INVENTORY_URL, title="A docs")

    summary = (
        await factory.create_intersphinx_ingest_service().ingest_sources()
    )

    assert summary.failed == 1
    assert summary.results[0].cache_status is None
    assert summary.stale_count == 0


@pytest.mark.asyncio
async def test_ingest_waits_for_a_concurrent_registration_write(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """An ingest fetches without the lock, then queues behind the row's writer.

    The registration is rewritten on another session and left uncommitted,
    which is the shape of an ingest already replacing this site's links.
    Two things have to be true of the ingest that meets it: it must have
    reached the origin before it started waiting -- no lock is held across
    an HTTP fetch -- and once it is let through it must build its links from
    the row as it now stands rather than the one it was handed, which is
    what makes the site's new title the one its links carry.

    The concurrent write is an ``UPDATE`` of a non-key column, so Postgres
    holds it as ``FOR NO KEY UPDATE``: it blocks the ingest's own
    ``SELECT ... FOR UPDATE`` and nothing else. The ``FOR KEY SHARE`` that
    the link rows' foreign key takes on the same row does not conflict with
    it, so an ingest that never locked the registration would sail past this
    and write links titled from the row it was handed.
    """
    route = _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="Old title"
    )
    source = await _get_source(factory, source_id)
    service = factory.create_intersphinx_ingest_service()

    async with _second_session() as writer:
        store = IntersphinxSourceStore(
            session=writer, logger=structlog.get_logger("test")
        )
        async with writer.begin():
            await store.update_source(source_id, title="New title")
            ingest = asyncio.create_task(service.ingest_source(source))
            # The origin answers while the registration is still locked, so
            # the fetch cannot have been made under the lock.
            await _wait_until(lambda: route.call_count == 1)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(
                    asyncio.shield(ingest), timeout=BLOCKED_GRACE
                )

        result = await asyncio.wait_for(ingest, timeout=UNBLOCKED_TIMEOUT)

    assert result is not None
    assert result.title == "New title"
    thing = await factory.create_intersphinx_entity_store().get_entity(
        "py", "pkg.mod.Thing"
    )
    assert thing is not None
    assert [link.collection_title for link in thing.links] == ["New title"]


@pytest.mark.asyncio
async def test_a_held_lock_does_not_block_another_source(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """One site's ingest does not wait on another site's.

    The registration row is the lock key precisely so that the sweep is not
    reduced to one site at a time by a lock the whole registry shares.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    _serve_inventory(
        respx_mock,
        OTHER_INVENTORY_URL,
        _inventory([("other.Thing", "class", "api.html#other.Thing")]),
    )
    locked_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    other_id = await _register_source(
        factory, url=OTHER_INVENTORY_URL, title="B docs"
    )
    other_source = await _get_source(factory, other_id)
    service = factory.create_intersphinx_ingest_service()

    async with _second_session() as holder:
        store = IntersphinxSourceStore(
            session=holder, logger=structlog.get_logger("test")
        )
        async with holder.begin():
            assert await store.lock_source(locked_id) is not None
            result = await asyncio.wait_for(
                service.ingest_source(other_source),
                timeout=UNBLOCKED_TIMEOUT,
            )

    assert result is not None
    assert result.status is SourceIngestStatus.success
    entity_store = factory.create_intersphinx_entity_store()
    assert await entity_store.get_entity("py", "other.Thing") is not None


@pytest.mark.asyncio
async def test_concurrent_ingests_leave_one_link_per_entity(
    factory: Factory,
    database_engine: AsyncEngine,
    respx_mock: respx.Router,
) -> None:
    """Two ingests of one site racing each other write one link per object.

    A replace is a delete followed by inserts, and two of them running
    together with nothing between them would each delete only what the other
    had already replaced -- leaving the site documenting every object twice.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    source = await _get_source(factory, source_id)

    async with _second_factory(database_engine) as other:
        results = await asyncio.gather(
            factory.create_intersphinx_ingest_service().ingest_source(source),
            other.create_intersphinx_ingest_service().ingest_source(source),
        )

    assert [result is not None for result in results] == [True, True]
    entity_store = factory.create_intersphinx_entity_store()
    for name in ("pkg", "pkg.mod", "pkg.mod.Thing", "pkg.Standalone"):
        entity = await entity_store.get_entity("py", name)
        assert entity is not None
        assert len(entity.links) == 1


async def _ingest_two_sites_documenting_one_object(
    factory: Factory, respx_mock: respx.Router
) -> tuple[int, int]:
    """Register and ingest two sites, one of which documents ``shared``.

    The starting state both write-race tests race from: site A documents
    ``apkg``, site B documents ``shared``, and both sites' inventories are
    then replaced by the ones the race re-ingests.

    Returns
    -------
    tuple of int
        The two registrations' IDs, site A's first.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, A_ONLY_INVENTORY)
    _serve_inventory(respx_mock, OTHER_INVENTORY_URL, SHARED_INVENTORY)
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    other_id = await _register_source(
        factory, url=OTHER_INVENTORY_URL, title="B docs"
    )
    await factory.create_intersphinx_ingest_service().ingest_sources()

    await _expire_cached_inventory(factory, INVENTORY_URL)
    await _expire_cached_inventory(factory, OTHER_INVENTORY_URL)
    _serve_inventory(respx_mock, INVENTORY_URL, A_AND_SHARED_INVENTORY)
    return source_id, other_id


@pytest.mark.asyncio
async def test_a_write_failure_costs_one_source_and_not_the_run(
    factory: Factory, respx_mock: respx.Router, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source whose write the database refuses is recorded and stepped over.

    The one way a source can fail that is not the site's fault and that the
    ingest cannot foresee: a link whose entity is gone by the time it is
    written. Losing that race must cost the site its refresh and nothing
    more -- not the sweep it was part of, and not the sources after it,
    which is what an escaping exception would take with it.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    _serve_inventory(
        respx_mock,
        OTHER_INVENTORY_URL,
        _inventory([("other.Thing", "class", "api.html#other.Thing")]),
    )
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    await _register_source(factory, url=OTHER_INVENTORY_URL, title="B docs")
    _link_to_a_missing_entity(monkeypatch, source_id=source_id)

    summary = (
        await factory.create_intersphinx_ingest_service().ingest_sources()
    )

    assert summary.failed == 1
    assert summary.succeeded == 1
    failed = next(
        result for result in summary.results if result.url == INVENTORY_URL
    )
    assert failed.status is SourceIngestStatus.failure
    assert failed.error is not None
    # The registry says so too, and the site after it was ingested.
    assert (
        await _get_source(factory, source_id)
    ).last_status is SourceIngestStatus.failure
    entity_store = factory.create_intersphinx_entity_store()
    assert await entity_store.get_entity("py", "other.Thing") is not None


@pytest.mark.asyncio
async def test_a_deregistration_cannot_prune_an_entity_an_ingest_linked(
    factory: Factory,
    database_engine: AsyncEngine,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deregistering one site keeps the links another site just committed.

    Site A picks up an object only site B documented, and site B is
    deregistered while A's ingest holds those links uncommitted. The
    deletion converges the whole entity graph, so it reaches an object whose
    only visible link is the one it is itself withdrawing -- and pruning it
    would cascade away A's link, which by then is committed and is the only
    record that A documents the object at all.

    Nothing recovers from that on its own: A's registration is stamped with
    the digest of the inventory it just ingested, so every later sweep
    recognizes those bytes and rewrites nothing.
    """
    source_id, other_id = await _ingest_two_sites_documenting_one_object(
        factory, respx_mock
    )
    source = await _get_source(factory, source_id)
    written, release = _pause_after_link_replace(
        monkeypatch, source_id=source_id
    )

    async with _second_factory(database_engine) as other:

        async def deregister() -> None:
            async with other.db_session.begin():
                service = other.create_intersphinx_source_service()
                assert await service.delete_source(other_id) is True

        ingest = asyncio.create_task(
            factory.create_intersphinx_ingest_service().ingest_source(source)
        )
        await asyncio.wait_for(written.wait(), timeout=UNBLOCKED_TIMEOUT)
        deletion = asyncio.create_task(deregister())
        # The deletion cannot finish while the ingest holds its links open:
        # its convergence has to be judged on what that ingest wrote.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                asyncio.shield(deletion), timeout=BLOCKED_GRACE
            )
        release.set()
        assert await asyncio.wait_for(ingest, timeout=UNBLOCKED_TIMEOUT)
        await asyncio.wait_for(deletion, timeout=UNBLOCKED_TIMEOUT)

    entity_store = factory.create_intersphinx_entity_store()
    shared = await entity_store.get_entity("py", "shared")
    assert shared is not None
    assert [link.collection_title for link in shared.links] == ["A docs"]


@pytest.mark.asyncio
async def test_an_ingest_cannot_prune_an_entity_another_ingest_linked(
    factory: Factory,
    database_engine: AsyncEngine,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two sites' ingests racing keep the links each of them wrote.

    The same window as a deregistration's, reached without deleting
    anything: every ingest converges the whole entity graph after replacing
    its own links, so site B's ingest prunes on a view of the links that
    predates site A's. Different sites lock different registrations, so
    nothing but the entity graph's own serialization stands between them.

    The two ingests must still overlap for the length of a fetch, which is
    what the origin's call count during the pause asserts: whatever
    serializes the writes must be taken after the inventory is in hand.
    """
    source_id, other_id = await _ingest_two_sites_documenting_one_object(
        factory, respx_mock
    )
    other_route = _serve_inventory(
        respx_mock, OTHER_INVENTORY_URL, B_ONLY_INVENTORY
    )
    # respx keys a route by its pattern, so this one already carries the
    # setup ingest's call; the fetch under test is the next one.
    fetches_before = other_route.call_count
    source = await _get_source(factory, source_id)
    other_source = await _get_source(factory, other_id)
    written, release = _pause_after_link_replace(
        monkeypatch, source_id=source_id
    )

    async with _second_factory(database_engine) as other:
        ingest = asyncio.create_task(
            factory.create_intersphinx_ingest_service().ingest_source(source)
        )
        await asyncio.wait_for(written.wait(), timeout=UNBLOCKED_TIMEOUT)
        other_ingest = asyncio.create_task(
            other.create_intersphinx_ingest_service().ingest_source(
                other_source
            )
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                asyncio.shield(other_ingest), timeout=BLOCKED_GRACE
            )
        # It reached its origin before it started waiting, so no ingest is
        # held up by another one's fetch.
        assert other_route.call_count == fetches_before + 1
        release.set()
        assert await asyncio.wait_for(ingest, timeout=UNBLOCKED_TIMEOUT)
        assert await asyncio.wait_for(other_ingest, timeout=UNBLOCKED_TIMEOUT)

    entity_store = factory.create_intersphinx_entity_store()
    shared = await entity_store.get_entity("py", "shared")
    assert shared is not None
    assert [link.collection_title for link in shared.links] == ["A docs"]
    assert await entity_store.get_entity("py", "bpkg") is not None


@pytest.mark.asyncio
async def test_a_source_deleted_mid_fetch_is_skipped(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A registration deleted while its inventory was in flight is not revived.

    Nothing about the site survives the delete: the sweep reports no outcome
    for it, its registration stays gone, and none of the objects its
    inventory described are stored -- which is what stops an ingest that
    started before the delete from writing links no registration owns.
    """
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    async with _second_session() as deleter:
        _delete_source_while_serving(
            respx_mock,
            INVENTORY_URL,
            PACKAGE_INVENTORY,
            session=deleter,
            source_id=source_id,
        )

        summary = (
            await factory.create_intersphinx_ingest_service().ingest_sources()
        )

    assert summary.results == []
    source_store = factory.create_intersphinx_source_store()
    assert await source_store.get_source(source_id) is None
    entity_store = factory.create_intersphinx_entity_store()
    assert await entity_store.get_entity("py", "pkg.mod.Thing") is None


@pytest.mark.asyncio
async def test_ingest_source_url_reports_a_source_deleted_mid_fetch(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """Naming a source that is deleted mid-ingest answers as unregistered.

    The caller asked Ook to ingest one named source; by the time there was
    anything to write, no such source was registered, which is the same
    answer a URL that was never registered gets.
    """
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    async with _second_session() as deleter:
        _delete_source_while_serving(
            respx_mock,
            INVENTORY_URL,
            PACKAGE_INVENTORY,
            session=deleter,
            source_id=source_id,
        )
        service = factory.create_intersphinx_ingest_service()

        with pytest.raises(NotFoundError):
            await service.ingest_source_url(INVENTORY_URL)

    entity_store = factory.create_intersphinx_entity_store()
    assert await entity_store.get_entity("py", "pkg.mod.Thing") is None


@pytest.mark.asyncio
async def test_a_source_repointed_mid_fetch_is_skipped(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A registration repointed mid-fetch has no links written for it.

    The links an inventory yields are resolved against the URL it was read
    from, so storing them under a registration that now names a different
    site would file one site's pages as another's. Nothing is written and
    the sweep reports no outcome, exactly as a deregistration in the same
    window gets none.
    """
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    async with _second_session() as repointer:
        _repoint_source_while_serving(
            respx_mock,
            INVENTORY_URL,
            PACKAGE_INVENTORY,
            session=repointer,
            source_id=source_id,
            new_url=OTHER_INVENTORY_URL,
        )

        summary = (
            await factory.create_intersphinx_ingest_service().ingest_sources()
        )

    assert summary.results == []
    entity_store = factory.create_intersphinx_entity_store()
    assert await entity_store.get_entity("py", "pkg.mod.Thing") is None


@pytest.mark.asyncio
async def test_a_source_repointed_mid_fetch_keeps_no_digest(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A repointed registration is not stamped with the old URL's digest.

    ``update_source`` cleared the digest when it moved the URL, and the
    ingest that was already reading the old URL must not put it back: a
    digest is a claim that the stored links were built from those bytes
    under this registration, which is the one thing that is no longer true.
    """
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    async with _second_session() as repointer:
        _repoint_source_while_serving(
            respx_mock,
            INVENTORY_URL,
            PACKAGE_INVENTORY,
            session=repointer,
            source_id=source_id,
            new_url=OTHER_INVENTORY_URL,
        )

        await factory.create_intersphinx_ingest_service().ingest_sources()

    source = await _get_source(factory, source_id)
    assert source.url == OTHER_INVENTORY_URL
    assert source.ingested_content_digest is None


@pytest.mark.asyncio
async def test_a_repointed_source_is_rebuilt_from_its_new_url(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """The sweep after a mid-fetch repoint reads the site's new URL.

    The two URLs serve byte-identical inventories, which is exactly the
    case a repoint is usually made for -- a mirror, or a site that moved --
    and an ``objects.inv`` records its project and version but not the base
    URL it is published at. So nothing but the registration's own URL
    distinguishes the two, and a digest stamped from the first fetch would
    make this sweep recognize the bytes and leave every link pointing into
    the directory the site moved off.
    """
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    _serve_inventory(respx_mock, OTHER_INVENTORY_URL, PACKAGE_INVENTORY)
    async with _second_session() as repointer:
        _repoint_source_while_serving(
            respx_mock,
            INVENTORY_URL,
            PACKAGE_INVENTORY,
            session=repointer,
            source_id=source_id,
            new_url=OTHER_INVENTORY_URL,
        )

        service = factory.create_intersphinx_ingest_service()
        await service.ingest_sources()
        summary = await service.ingest_sources()

    assert summary.succeeded == 1
    assert summary.unchanged_count == 0
    assert sorted(
        url
        for _, _, links in await _stored_python_domain(factory)
        for url in links
    ) == [
        "https://b.example/api.html#module-pkg",
        "https://b.example/api.html#module-pkg.mod",
        "https://b.example/api.html#pkg.Standalone",
        "https://b.example/api.html#pkg.mod.Thing",
    ]


@pytest.mark.asyncio
async def test_ingest_source_url_reports_a_source_repointed_mid_fetch(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """Naming a source repointed mid-ingest answers as unregistered.

    The caller named one inventory URL; by the time there was anything to
    write, no registration held it any more, which is the answer a URL that
    was never registered gets.
    """
    source_id = await _register_source(
        factory, url=INVENTORY_URL, title="A docs"
    )
    async with _second_session() as repointer:
        _repoint_source_while_serving(
            respx_mock,
            INVENTORY_URL,
            PACKAGE_INVENTORY,
            session=repointer,
            source_id=source_id,
            new_url=OTHER_INVENTORY_URL,
        )
        service = factory.create_intersphinx_ingest_service()

        with pytest.raises(NotFoundError):
            await service.ingest_source_url(INVENTORY_URL)

    entity_store = factory.create_intersphinx_entity_store()
    assert await entity_store.get_entity("py", "pkg.mod.Thing") is None
