"""Tests for the IntersphinxIngestService."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import respx
import sphobjinv
from httpx import Response

from ook.domain.intersphinx import (
    IntersphinxInventory,
    InventoryCacheStatus,
    InventoryFetchStatus,
)
from ook.domain.intersphinxentities import SPHINX_DOMAIN_HIERARCHIES
from ook.domain.intersphinxsources import SourceIngestStatus
from ook.domain.links import Link
from ook.exceptions import NotFoundError
from ook.factory import Factory
from ook.services.ingest.intersphinx import SPHINX_DOMAIN_LINK_TYPES

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
async def test_reingest_is_idempotent(
    factory: Factory, respx_mock: respx.Router
) -> None:
    """Running ingest twice leaves one link per object, not two."""
    _serve_inventory(respx_mock, INVENTORY_URL, PACKAGE_INVENTORY)
    await _register_source(factory, url=INVENTORY_URL, title="A docs")

    service = factory.create_intersphinx_ingest_service()
    first = await service.ingest_sources()
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
