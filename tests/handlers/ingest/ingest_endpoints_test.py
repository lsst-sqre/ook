"""Tests for the /ook/ingest endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from ook.domain.base32id import serialize_ook_base32_id
from tests.support.github import GitHubMocker


@pytest.mark.asyncio
async def test_post_ingest_sdm_schemas(
    client: AsyncClient, mock_github: GitHubMocker
) -> None:
    """Test ``POST /ook/ingest/sdm-schemas``."""
    mock_github.mock_sdm_schema_release_ingest()

    # Ingest the SDM schemas
    response = await client.post(
        "/ook/ingest/sdm-schemas", json={"github_release_tag": None}
    )
    assert response.status_code == 200

    # Check links for a schema
    response = await client.get(
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs"
    )
    assert response.status_code == 200
    data = response.json()
    assert data[0] == {
        "url": "https://sdm-schemas.lsst.io/dp02.html",
        "title": "dp02_dc2_catalogs schema",
        "type": "schema_browser",
        "collection_title": "Science Data Model Schemas",
    }

    # Check links for a table
    response = await client.get(
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables/Object"
    )
    assert response.status_code == 200
    data = response.json()
    assert data[0] == {
        "url": "https://sdm-schemas.lsst.io/dp02.html#Object",
        "title": "Object table",
        "type": "schema_browser",
        "collection_title": "Science Data Model Schemas",
    }

    # Check links for a column
    response = await client.get(
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables/Visit/columns/ra"
    )
    assert response.status_code == 200
    data = response.json()
    assert data[0] == {
        "url": "https://sdm-schemas.lsst.io/dp02.html#Visit.ra",
        "title": "ra column",
        "type": "schema_browser",
        "collection_title": "Science Data Model Schemas",
    }


@pytest.mark.asyncio
async def test_post_ingest_documents_created_and_updated(
    client: AsyncClient,
) -> None:
    """Documents report ``created`` when new and ``updated`` on re-ingest."""
    first_batch = {
        "documents": [
            {
                "title": "First Document",
                "series": "Test Series",
                "handle": "TESTDOC-100",
                "number": 100,
            }
        ]
    }
    response = await client.post(
        "/ook/ingest/resources/documents", json=first_batch
    )
    assert response.status_code == 200, response.text
    first_results = response.json()
    assert len(first_results) == 1
    assert first_results[0]["status"] == "created"
    assert first_results[0]["handle"] == "TESTDOC-100"
    assert first_results[0]["resource"] is not None
    assert first_results[0]["error"] is None
    created_id = first_results[0]["resource"]["id"]

    # Second batch re-ingests the same document (matched by series + handle)
    # and adds a genuinely new document.
    second_batch = {
        "documents": [
            {
                "title": "First Document (revised)",
                "series": "Test Series",
                "handle": "TESTDOC-100",
                "number": 100,
            },
            {
                "title": "Second Document",
                "series": "Test Series",
                "handle": "TESTDOC-200",
                "number": 200,
            },
        ]
    }
    response = await client.post(
        "/ook/ingest/resources/documents", json=second_batch
    )
    assert response.status_code == 200, response.text
    second_results = response.json()
    assert len(second_results) == 2

    updated_result = second_results[0]
    assert updated_result["status"] == "updated"
    assert updated_result["handle"] == "TESTDOC-100"
    # Idempotent: the re-ingested document keeps its original ID.
    assert updated_result["resource"]["id"] == created_id
    assert updated_result["resource"]["title"] == "First Document (revised)"

    new_result = second_results[1]
    assert new_result["status"] == "created"
    assert new_result["handle"] == "TESTDOC-200"
    assert new_result["resource"]["id"] != created_id


@pytest.mark.asyncio
async def test_post_ingest_documents_mixed_success(
    client: AsyncClient,
) -> None:
    """A malformed document fails in isolation without dropping the batch."""
    # A valid Base32 ID for a resource that does not exist. A relation to it
    # violates the resource_relation foreign key during the document's upsert,
    # so that single item fails while the others succeed.
    missing_resource_id = serialize_ook_base32_id(1)

    batch = {
        "documents": [
            {
                "title": "Good Document One",
                "series": "Batch Series",
                "handle": "BATCH-001",
                "number": 1,
            },
            {
                "title": "Bad Document",
                "series": "Batch Series",
                "handle": "BATCH-002",
                "number": 2,
                "resource_relations": [
                    {
                        "relation_type": "Cites",
                        "resource_id": missing_resource_id,
                    }
                ],
            },
            {
                "title": "Good Document Two",
                "series": "Batch Series",
                "handle": "BATCH-003",
                "number": 3,
            },
        ]
    }
    response = await client.post("/ook/ingest/resources/documents", json=batch)
    assert response.status_code == 200, response.text
    results = response.json()
    assert len(results) == 3

    assert results[0]["status"] == "created"
    assert results[0]["handle"] == "BATCH-001"
    assert results[0]["resource"] is not None

    failed = results[1]
    assert failed["status"] == "failed"
    assert failed["handle"] == "BATCH-002"
    assert failed["resource"] is None
    assert failed["error"]  # non-empty error detail
    # The sanitized detail must not leak the SQL statement or bound
    # parameters that SQLAlchemy appends to the exception string.
    assert "[SQL" not in failed["error"]
    assert "parameters" not in failed["error"]

    assert results[2]["status"] == "created"
    assert results[2]["handle"] == "BATCH-003"
    assert results[2]["resource"] is not None

    # Both good documents persisted; the failing one did not abort them.
    good_one_id = results[0]["resource"]["id"]
    good_two_id = results[2]["resource"]["id"]
    for resource_id in (good_one_id, good_two_id):
        get_response = await client.get(f"/ook/resources/{resource_id}")
        assert get_response.status_code == 200


@pytest.mark.asyncio
async def test_post_ingest_lsst_texmf(
    client: AsyncClient, mock_github: GitHubMocker
) -> None:
    """Test ``POST /ook/ingest/sdm-schemas``."""
    mock_github.mock_lsst_texmf_ingest()

    # Ingest the lsst/lsst-texmf repo (authordb.yaml and glossary)
    response = await client.post("/ook/ingest/lsst-texmf", json={})
    assert response.status_code == 200

    # Check that we got an author
    response = await client.get("/ook/authors/sickj")
    assert response.status_code == 200
    data = response.json()
    assert data["family_name"] == "Sick"
    assert len(data["affiliations"]) == 2

    # Check that TeX is decoded to unicode
    response = await client.get("/ook/authors/ivezicv")
    assert response.status_code == 200
    data = response.json()
    assert data["family_name"] == "Ivezić"
    assert data["given_name"] == "Željko"

    # Check that we got a glossary term
    response = await client.get("/ook/glossary/search", params={"q": "square"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["term"] == "SQuaRE"
