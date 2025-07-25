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
                        "author": author1.model_dump(),
                        "role": "Creator",
                        "order": 1,
                    },
                    {
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
async def test_get_resource_with_related_resources(
    client: AsyncClient, mock_github: GitHubMocker
) -> None:
    """Test that related resources are included in API responses.

    This test demonstrates:
    1. Creating two resources where one cites the other
    2. Verifying that the API returns related resources with summaries
    """
    # Add authordb data needed by the resources API
    mock_github.mock_lsst_texmf_ingest()
    response = await client.post(
        "/ook/ingest/lsst-texmf", json={"ingest_glossary": False}
    )
    assert response.status_code == 200

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

    # Step 1: Create Document B first (to be referenced by Document A)
    document_b_data = {
        "documents": [
            {
                "title": "Foundational Research Methods",
                "description": (
                    "A comprehensive guide to research methodologies."
                ),
                "url": "https://example.org/documents/research-methods",
                "doi": "https://doi.org/10.1000/foundation456",
                "date_resource_published": "2023-06-15T10:00:00Z",
                "version": "2.1",
                "type": "Book",
                "series": "Research Guides",
                "handle": "RG-001",
                "generator": "Documenteer 2.0.0",
                "contributors": [
                    {
                        "author": author2.model_dump(),
                        "role": "Creator",
                        "order": 1,
                    }
                ],
                "resource_relations": [],  # No internal relations
                "external_relations": [],  # No external relations
            }
        ]
    }

    # Ingest Document B
    response_b = await client.post(
        "/ook/ingest/resources/documents", json=document_b_data
    )
    assert response_b.status_code == 200
    document_b = response_b.json()[0]
    document_b_id = document_b["id"]

    # Step 2: Create Document A with relation to Document B
    document_a_data = {
        "documents": [
            {
                "title": "Advanced Research Applications",
                "description": (
                    "Practical applications building on foundational methods."
                ),
                "url": "https://example.org/documents/advanced-applications",
                "doi": "https://doi.org/10.1000/applications123",
                "date_resource_published": "2024-02-10T14:30:00Z",
                "version": "1.0",
                "type": "Report",
                "series": "Applied Research",
                "handle": "AR-042",
                "generator": "Documenteer 2.0.0",
                "contributors": [
                    {
                        "author": author1.model_dump(),
                        "role": "Creator",
                        "order": 1,
                    }
                ],
                "resource_relations": [
                    {"relation_type": "Cites", "resource_id": document_b_id}
                ],
                "external_relations": [
                    {
                        "relation_type": "References",
                        "external_reference": {
                            "title": (
                                "Machine Learning for Astronomical Data "
                                "Analysis"
                            ),
                            "doi": "https://doi.org/10.1093/mnras/stab123",
                            "url": (
                                "https://academic.oup.com/mnras/article/"
                                "504/2/2345/6234567"
                            ),
                            "type": "JournalArticle",
                            "publication_year": "2021",
                            "volume": "504",
                            "issue": "2",
                            "number_type": "Article",
                            "first_page": "2345",
                            "last_page": "2361",
                            "publisher": "Oxford University Press",
                            "contributors": [
                                {
                                    "name": "John Smith",
                                    "type": "Personal",
                                    "given_name": "John",
                                    "surname": "Smith",
                                    "orcid": (
                                        "https://orcid.org/0000-0002-1234-5678"
                                    ),
                                    "role": "Creator",
                                    "affiliations": [
                                        {
                                            "name": "Harvard University",
                                            "ror": "https://ror.org/03vek6s52",
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                ],
            }
        ]
    }

    # Ingest Document A
    response_a = await client.post(
        "/ook/ingest/resources/documents", json=document_a_data
    )
    assert response_a.status_code == 200
    document_a = response_a.json()[0]
    document_a_id = document_a["id"]

    # Step 3: Test API response for Document A (has internal relation)
    response_a = await client.get(f"/ook/resources/{document_a_id}")
    assert response_a.status_code == 200
    retrieved_doc_a = response_a.json()

    # Verify Document A has the related field with both
    # internal and external relations
    assert "related" in retrieved_doc_a
    assert len(retrieved_doc_a["related"]) == 2

    # Find the internal and external relations in the related array
    internal_relation = None
    external_relation = None

    for relation in retrieved_doc_a["related"]:
        if relation["relation_type"] == "Cites":
            internal_relation = relation
        elif relation["relation_type"] == "References":
            external_relation = relation

    # Verify internal resource relation (Document A cites Document B)
    assert internal_relation is not None
    assert internal_relation["relation_type"] == "Cites"
    assert internal_relation["resource_type"] == "resource"

    # Verify the related resource is returned as a summary (has self_url)
    assert "self_url" in internal_relation["resource"]
    assert internal_relation["resource"]["id"] == document_b_id
    assert (
        internal_relation["resource"]["title"]
        == "Foundational Research Methods"
    )
    assert (
        internal_relation["resource"]["description"]
        == "A comprehensive guide to research methodologies."
    )
    assert (
        internal_relation["resource"]["url"]
        == "https://example.org/documents/research-methods"
    )
    assert (
        internal_relation["resource"]["doi"]
        == "https://doi.org/10.1000/foundation456"
    )
    assert (
        f"/ook/resources/{document_b_id}"
        in internal_relation["resource"]["self_url"]
    )

    # Verify external resource relation (Document A references external paper)
    assert external_relation is not None
    assert external_relation["relation_type"] == "References"
    assert external_relation["resource_type"] == "external"

    # Verify the external reference details
    ext_ref = external_relation["resource"]
    assert (
        ext_ref["title"] == "Machine Learning for Astronomical Data Analysis"
    )
    assert ext_ref["doi"] == "https://doi.org/10.1093/mnras/stab123"
    assert (
        ext_ref["url"]
        == "https://academic.oup.com/mnras/article/504/2/2345/6234567"
    )
    assert ext_ref["type"] == "JournalArticle"
    assert ext_ref["publication_year"] == "2021"
    assert ext_ref["volume"] == "504"
    assert ext_ref["issue"] == "2"
    assert ext_ref["number_type"] == "Article"
    assert ext_ref["first_page"] == "2345"
    assert ext_ref["last_page"] == "2361"
    assert ext_ref["publisher"] == "Oxford University Press"

    # Verify external contributor details
    assert len(ext_ref["contributors"]) == 1
    contributor = ext_ref["contributors"][0]
    assert contributor["name"] == "John Smith"
    assert contributor["type"] == "Personal"
    assert contributor["given_name"] == "John"
    assert contributor["surname"] == "Smith"
    assert contributor["orcid"] == "https://orcid.org/0000-0002-1234-5678"
    assert contributor["role"] == "Creator"
    assert len(contributor["affiliations"]) == 1
    assert contributor["affiliations"][0]["name"] == "Harvard University"
    assert contributor["affiliations"][0]["ror"] == "https://ror.org/03vek6s52"

    # Step 4: Test API response for Document B (has no relations)
    response_b = await client.get(f"/ook/resources/{document_b_id}")
    assert response_b.status_code == 200
    retrieved_doc_b = response_b.json()

    # Verify Document B has the related field but it's empty
    assert "related" in retrieved_doc_b
    assert len(retrieved_doc_b["related"]) == 0

    # Step 5: Verify that the original document fields are still present
    # Document A verification
    assert retrieved_doc_a["id"] == document_a_id
    assert retrieved_doc_a["title"] == "Advanced Research Applications"
    assert retrieved_doc_a["resource_class"] == "document"
    assert retrieved_doc_a["series"] == "Applied Research"
    assert retrieved_doc_a["handle"] == "AR-042"

    # Document B verification
    assert retrieved_doc_b["id"] == document_b_id
    assert retrieved_doc_b["title"] == "Foundational Research Methods"
    assert retrieved_doc_b["resource_class"] == "document"
    assert retrieved_doc_b["series"] == "Research Guides"
    assert retrieved_doc_b["handle"] == "RG-001"


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
