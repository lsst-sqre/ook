"""Tests for the /ook/resources endpoints."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from ook.domain.authors import Author
from tests.support.github import GitHubMocker


@pytest.mark.asyncio
async def test_get_resource_by_id(
    client: AsyncClient, mock_github: GitHubMocker
) -> None:
    """Test ``GET /ook/resources/{id}`` endpoint."""
    # Add authordb data needed by the resources API
    mock_github.mock_lsst_texmf_ingest()
    response = await client.post(
        "/ook/ingest/lsst-texmf", json={"ingest_glossary": False}
    )
    assert response.status_code == 200
    # Define test constants
    expected_description = (
        "This is a sample technical note for testing purposes."
    )

    # Create sample authors for contributors
    author1 = Author.model_validate(
        {
            "internal_id": "economouf",
            "surname": "Economou",
            "given_name": None,
        }
    )

    author2 = Author.model_validate(
        {
            "internal_id": "sickj",
            "surname": "Sick",
            "given_name": None,
        }
    )

    # Create sample document data for ingestion
    document_data = {
        "documents": [
            {
                "title": "A Sample Technical Note",
                "description": expected_description,
                "url": "https://example.org/documents/sample-001",
                "doi": "https://doi.org/10.1000/test123",
                "date_resource_published": "2024-01-15T10:00:00Z",
                "date_resource_updated": "2024-01-20T15:30:00Z",
                "version": "1.0",
                "type": "Dataset",
                "series": "Technical Notes",
                "handle": "TEST-001",
                "generator": "Documenteer 2.0.0",
                "contributors": [
                    {
                        "resource_id": 0,  # Will be updated after creation
                        "author": author1.model_dump(),
                        "role": "Creator",
                        "order": 1,
                    },
                    {
                        "resource_id": 0,  # Will be updated after creation
                        "author": author2.model_dump(),
                        "role": "Editor",
                        "order": 2,
                    },
                ],
            }
        ]
    }

    # Ingest the document using POST /ingest/resources/documents
    response = await client.post(
        "/ook/ingest/resources/documents", json=document_data
    )
    response_data = response.json()
    assert response.status_code == 200, json.dumps(response_data, indent=2)

    ingested_documents = response.json()
    assert len(ingested_documents) == 1

    ingested_doc = ingested_documents[0]
    document_id = ingested_doc["id"]

    # Verify the document was ingested correctly
    assert ingested_doc["title"] == "A Sample Technical Note"
    assert ingested_doc["description"] == expected_description
    assert ingested_doc["url"] == "https://example.org/documents/sample-001"
    assert ingested_doc["doi"] == "https://doi.org/10.1000/test123"
    assert ingested_doc["version"] == "1.0"
    assert ingested_doc["type"] == "Dataset"
    assert ingested_doc["series"] == "Technical Notes"
    assert ingested_doc["handle"] == "TEST-001"
    assert ingested_doc["generator"] == "Documenteer 2.0.0"
    assert ingested_doc["resource_class"] == "document"

    # Now test the GET /resources/{id} endpoint
    response = await client.get(f"/ook/resources/{document_id}")
    assert response.status_code == 200

    retrieved_doc = response.json()

    # Verify that the retrieved document matches the ingested document
    assert retrieved_doc["id"] == document_id
    assert retrieved_doc["title"] == "A Sample Technical Note"
    assert retrieved_doc["description"] == expected_description
    assert retrieved_doc["url"] == "https://example.org/documents/sample-001"
    assert retrieved_doc["doi"] == "https://doi.org/10.1000/test123"
    assert retrieved_doc["version"] == "1.0"
    assert retrieved_doc["type"] == "Dataset"
    assert retrieved_doc["series"] == "Technical Notes"
    assert retrieved_doc["handle"] == "TEST-001"
    assert retrieved_doc["generator"] == "Documenteer 2.0.0"
    assert retrieved_doc["resource_class"] == "document"

    # Verify timestamps are present in new schema format
    assert "date_published" in retrieved_doc
    assert "date_updated" in retrieved_doc
    assert "metadata" in retrieved_doc
    assert "date_created" in retrieved_doc["metadata"]
    assert "date_updated" in retrieved_doc["metadata"]

    # Verify self_url is present
    assert "self_url" in retrieved_doc
    assert f"/ook/resources/{document_id}" in retrieved_doc["self_url"]

    # Verify contributors match new schema (creators separate from others)
    assert "creators" in retrieved_doc
    assert "contributors" in retrieved_doc
    assert len(retrieved_doc["creators"]) == 1  # One creator

    # Verify creator details match
    retrieved_creator = retrieved_doc["creators"][0]
    assert retrieved_creator["family_name"] == "Economou"
    assert retrieved_creator["given_name"] == "Frossie"
    # TODO(jonathansick) This is the ORCID ID not URL because we're returning
    # the Author domain object directly.
    assert (
        retrieved_creator["orcid"] == "https://orcid.org/0000-0002-8333-7615"
    )
    assert retrieved_creator["affiliations"][0]["internal_id"] == "RubinObs"

    # Verify editor is in contributors dict under Editor role
    assert "Editor" in retrieved_doc["contributors"]
    assert len(retrieved_doc["contributors"]["Editor"]) == 1

    retrieved_editor = retrieved_doc["contributors"]["Editor"][0]
    assert retrieved_editor["family_name"] == "Sick"
    assert retrieved_editor["given_name"] == "Jonathan"
    # TODO(jonathansick) This is the ORCID ID not URL because we're returning
    # the Author domain object directly.
    assert retrieved_editor["orcid"] == "https://orcid.org/0000-0003-3001-676X"


@pytest.mark.asyncio
async def test_get_resource_by_id_not_valid(client: AsyncClient) -> None:
    """Test ``GET /ook/resources/{id}`` with non-existent ID."""
    # Use a fake Base32 ID that doesn't exist; this returns a 422 error because
    # the ID is not valid.
    fake_id = "fake-id12-3456-78ab"

    response = await client.get(f"/ook/resources/{fake_id}")
    response_data = response.json()
    assert response.status_code == 422, json.dumps(response_data, indent=2)

    error_data = response.json()
    assert "detail" in error_data
    assert "value error" in error_data["detail"][0]["msg"].lower()
