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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ook.config import config
from ook.dbschema.intersphinxentities import SqlIntersphinxEntity
from ook.dbschema.links import SqlIntersphinxLink
from ook.domain.intersphinx import (
    IntersphinxInventory,
    InventoryCacheStatus,
    InventoryFetchStatus,
)
from ook.domain.intersphinxentities import SPHINX_DOMAIN_HIERARCHIES
from ook.domain.intersphinxsources import IntersphinxSource, SourceIngestStatus
from ook.domain.links import Link
from ook.exceptions import NotFoundError
from ook.factory import Factory
from ook.services.ingest.intersphinx import SPHINX_DOMAIN_LINK_TYPES
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
async def test_reingest_prunes_objects_the_site_dropped(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """An object gone from the inventory loses its link and its entity.

    A parent that goes with it survives when a descendant is still
    documented: a site is free to stop publishing a module's own page while
    still documenting its classes, and deleting the module would take the
    classes' place in the hierarchy with it.
    """
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    await _register_source(factory, url=INVENTORY_URL, title="A docs")
    service = factory.create_intersphinx_ingest_service()
    await service.ingest_sources()

    # The site stops documenting pkg.Standalone and pkg.mod's own page.
    await _expire_cached_inventory(factory, INVENTORY_URL)
    _serve_inventory(
        respx_mock,
        INVENTORY_URL,
        _inventory(
            [
                ("pkg", "module", "api.html#module-pkg"),
                ("pkg.mod.Thing", "class", "api.html#pkg.mod.Thing"),
            ]
        ),
    )

    summary = await service.ingest_sources()

    assert summary.pruned_count == 1
    entity_store = factory.create_intersphinx_entity_store()
    assert await entity_store.get_entity("py", "pkg.Standalone") is None
    # pkg.mod holds no link of its own any more but still contains a
    # documented class, so it stays.
    module = await entity_store.get_entity("py", "pkg.mod")
    assert module is not None
    assert module.links == []
    thing = await entity_store.get_entity("py", "pkg.mod.Thing")
    assert thing is not None
    assert thing.parent_name == "pkg.mod"


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
async def test_a_sweep_of_skipped_sources_still_prunes_orphans(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """A deregistered site's entities go even when no source is re-ingested.

    Deleting a registration takes its links with it and leaves the entities
    they pointed at behind for the pruning path. Every other source
    recognizing its inventory must not be what keeps those entities alive.
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
