"""Tests for the /ook/intersphinx/sources registry endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from ook.config import config
from ook.domain.intersphinxsources import SourceIngestStatus
from ook.factory import Factory

SOURCES_URL = f"{config.path_prefix}/intersphinx/sources"
"""The registry collection endpoint."""

INVENTORY_URL = "https://pipelines.lsst.io/v/weekly/objects.inv"
"""An inventory URL used across the registry tests."""


@pytest.mark.asyncio
async def test_register_source_returns_the_registration(
    client: AsyncClient,
) -> None:
    """POSTing a URL and title registers a source and returns it."""
    response = await client.post(
        SOURCES_URL,
        json={"url": INVENTORY_URL, "title": "Rubin Science Pipelines"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["url"] == INVENTORY_URL
    assert data["title"] == "Rubin Science Pipelines"
    assert data["enabled"] is True
    # A source that has never been ingested is distinguishable from one
    # whose last ingest succeeded, so it reads as pending rather than
    # healthy.
    assert data["date_ingested"] is None
    assert data["last_status"] is None
    assert data["last_error"] is None
    assert data["self_url"].endswith(f"{SOURCES_URL}/{data['id']}")
    assert response.headers["Location"] == data["self_url"]


@pytest.mark.asyncio
async def test_registering_a_duplicate_url_is_a_conflict(
    client: AsyncClient,
) -> None:
    """The inventory URL is the registration's identity, so registering it
    twice is a conflict rather than a second row.
    """
    first = await client.post(
        SOURCES_URL,
        json={"url": INVENTORY_URL, "title": "Rubin Science Pipelines"},
    )
    assert first.status_code == 201

    duplicate = await client.post(
        SOURCES_URL,
        json={"url": INVENTORY_URL, "title": "A second registration"},
    )

    assert duplicate.status_code == 409
    # The rejection names the URL that is already taken, so an operator
    # scripting registrations can tell which one collided.
    assert INVENTORY_URL in duplicate.text

    # The conflict left the registry with the original registration alone.
    listing = await client.get(SOURCES_URL)
    assert listing.status_code == 200
    assert [source["title"] for source in listing.json()] == [
        "Rubin Science Pipelines"
    ]


@pytest.mark.asyncio
async def test_sources_are_listed_by_inventory_url(
    client: AsyncClient,
) -> None:
    """The listing is ordered by inventory URL, not by registration order."""
    for url, title in (
        ("https://zzz.lsst.io/objects.inv", "Last alphabetically"),
        ("https://aaa.lsst.io/objects.inv", "First alphabetically"),
    ):
        created = await client.post(
            SOURCES_URL, json={"url": url, "title": title}
        )
        assert created.status_code == 201

    response = await client.get(SOURCES_URL)

    assert response.status_code == 200
    assert [source["url"] for source in response.json()] == [
        "https://aaa.lsst.io/objects.inv",
        "https://zzz.lsst.io/objects.inv",
    ]


@pytest.mark.asyncio
async def test_listing_can_be_narrowed_to_enabled_sources(
    client: AsyncClient,
) -> None:
    """``enabled_only`` lists just the sources the next run will visit."""
    enabled = await client.post(
        SOURCES_URL,
        json={"url": INVENTORY_URL, "title": "Rubin Science Pipelines"},
    )
    assert enabled.status_code == 201
    parked = await client.post(
        SOURCES_URL,
        json={
            "url": "https://parked.lsst.io/objects.inv",
            "title": "Parked site",
            "enabled": False,
        },
    )
    assert parked.status_code == 201
    assert parked.json()["enabled"] is False

    everything = await client.get(SOURCES_URL)
    assert everything.status_code == 200
    assert len(everything.json()) == 2

    response = await client.get(SOURCES_URL, params={"enabled_only": True})

    assert response.status_code == 200
    assert [source["title"] for source in response.json()] == [
        "Rubin Science Pipelines"
    ]


@pytest.mark.asyncio
async def test_get_source_returns_the_registration(
    client: AsyncClient,
) -> None:
    """A registration is readable at the ``self_url`` it advertises."""
    created = await client.post(
        SOURCES_URL,
        json={"url": INVENTORY_URL, "title": "Rubin Science Pipelines"},
    )
    assert created.status_code == 201

    response = await client.get(created.json()["self_url"])

    assert response.status_code == 200
    assert response.json() == created.json()


@pytest.mark.asyncio
async def test_get_unknown_source_is_not_found(client: AsyncClient) -> None:
    """An unregistered ID is a 404 rather than an empty registration."""
    response = await client.get(f"{SOURCES_URL}/12345")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_writes_only_the_fields_it_names(
    client: AsyncClient,
) -> None:
    """A partial update leaves the fields it does not mention alone."""
    created = await client.post(
        SOURCES_URL,
        json={"url": INVENTORY_URL, "title": "Rubin Science Pipelines"},
    )
    assert created.status_code == 201
    self_url = created.json()["self_url"]

    retitled = await client.patch(self_url, json={"title": "LSST Pipelines"})

    assert retitled.status_code == 200
    assert retitled.json()["title"] == "LSST Pipelines"
    # Retitling did not have to restate the enabled flag to keep it.
    assert retitled.json()["enabled"] is True
    assert retitled.json()["url"] == INVENTORY_URL

    parked = await client.patch(self_url, json={"enabled": False})

    assert parked.status_code == 200
    assert parked.json()["enabled"] is False
    assert parked.json()["title"] == "LSST Pipelines"


@pytest.mark.asyncio
async def test_update_onto_a_registered_url_is_a_conflict(
    client: AsyncClient,
) -> None:
    """Moving a registration onto another's URL is refused, like a
    duplicate registration is.
    """
    first = await client.post(
        SOURCES_URL,
        json={"url": INVENTORY_URL, "title": "Rubin Science Pipelines"},
    )
    assert first.status_code == 201
    second = await client.post(
        SOURCES_URL,
        json={"url": "https://dmtn.lsst.io/objects.inv", "title": "Technote"},
    )
    assert second.status_code == 201

    response = await client.patch(
        second.json()["self_url"], json={"url": INVENTORY_URL}
    )

    assert response.status_code == 409

    # The refused move left both registrations as they were.
    unchanged = await client.get(second.json()["self_url"])
    assert unchanged.status_code == 200
    assert unchanged.json()["url"] == "https://dmtn.lsst.io/objects.inv"


@pytest.mark.asyncio
async def test_update_unknown_source_is_not_found(
    client: AsyncClient,
) -> None:
    """Updating an unregistered ID is a 404 rather than a registration."""
    response = await client.patch(
        f"{SOURCES_URL}/12345", json={"title": "Nowhere"}
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_removes_the_registration(client: AsyncClient) -> None:
    """Deleting a registration takes it out of the registry."""
    created = await client.post(
        SOURCES_URL,
        json={"url": INVENTORY_URL, "title": "Rubin Science Pipelines"},
    )
    assert created.status_code == 201
    self_url = created.json()["self_url"]

    response = await client.delete(self_url)

    assert response.status_code == 204
    assert (await client.get(self_url)).status_code == 404
    assert (await client.get(SOURCES_URL)).json() == []


@pytest.mark.asyncio
async def test_delete_unknown_source_is_not_found(
    client: AsyncClient,
) -> None:
    """Deleting an unregistered ID is a 404 rather than a silent success."""
    response = await client.delete(f"{SOURCES_URL}/12345")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_ingest_outcome_surfaces_on_the_registration(
    client: AsyncClient, factory: Factory
) -> None:
    """The observability fields report the most recent ingest attempt."""
    created = await client.post(
        SOURCES_URL,
        json={"url": INVENTORY_URL, "title": "Rubin Science Pipelines"},
    )
    assert created.status_code == 201

    date_ingested = datetime(2026, 9, 2, 17, 5, tzinfo=UTC)
    store = factory.create_intersphinx_source_store()
    recorded = await store.record_ingest_outcome(
        created.json()["id"],
        date_ingested=date_ingested,
        status=SourceIngestStatus.failure,
        error="404 Not Found",
    )
    assert recorded is True
    await factory.db_session.commit()

    response = await client.get(created.json()["self_url"])

    assert response.status_code == 200
    data = response.json()
    assert datetime.fromisoformat(data["date_ingested"]) == date_ingested
    assert data["last_status"] == "failure"
    assert data["last_error"] == "404 Not Found"


@pytest.mark.asyncio
async def test_observability_fields_are_not_writable(
    client: AsyncClient,
) -> None:
    """A registration cannot claim an ingest outcome it never had."""
    response = await client.post(
        SOURCES_URL,
        json={
            "url": INVENTORY_URL,
            "title": "Rubin Science Pipelines",
            "last_status": "success",
        },
    )

    # Refused rather than silently dropped: a client that seeded a status
    # and got a 201 would believe the claim had been recorded.
    assert response.status_code == 422
    assert "last_status" in response.text


@pytest.mark.asyncio
async def test_observability_fields_are_not_writable_by_update(
    client: AsyncClient,
) -> None:
    """An update cannot clear the failure its own source is showing."""
    created = await client.post(
        SOURCES_URL,
        json={"url": INVENTORY_URL, "title": "Rubin Science Pipelines"},
    )
    assert created.status_code == 201

    response = await client.patch(
        created.json()["self_url"], json={"last_error": None}
    )

    assert response.status_code == 422
    assert "last_error" in response.text


@pytest.mark.asyncio
async def test_non_https_inventory_url_is_rejected(
    client: AsyncClient,
) -> None:
    """An ``http`` inventory URL could only ever fail its ingest.

    Every registered inventory is fetched through the intersphinx cache
    service, whose SSRF guard refuses anything but ``https``, so the scheme
    is caught here rather than left to surface as a stamped failure on the
    next scheduled run.
    """
    response = await client.post(
        SOURCES_URL,
        json={
            "url": "http://pipelines.lsst.io/objects.inv",
            "title": "Rubin Science Pipelines",
        },
    )

    assert response.status_code == 422
    assert (await client.get(SOURCES_URL)).json() == []


@pytest.mark.asyncio
async def test_openapi_documents_the_admin_scope_on_writes(
    client: AsyncClient,
) -> None:
    """The write endpoints publish the scope the ingress enforces.

    Registering a site commits Ook to serving links from it, so the
    registry's writes are gated by ``exec:admin`` rather than by the
    ``write:intersphinx`` scope that merely warms the inventory cache.
    Nothing in Ook enforces that -- it is Gafaelfawr ingress
    configuration -- so the published API is where the requirement is
    stated.
    """
    response = await client.get(f"{config.path_prefix}/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    writes = [
        paths[SOURCES_URL]["post"],
        paths[f"{SOURCES_URL}/{{source_id}}"]["patch"],
        paths[f"{SOURCES_URL}/{{source_id}}"]["delete"],
    ]
    for operation in writes:
        assert "exec:admin" in operation["description"]
        assert "write:intersphinx" in operation["description"]
