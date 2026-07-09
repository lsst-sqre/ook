"""Tests for the /ook/admin/authors/aliases endpoints."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.support.github import GitHubMocker


@pytest_asyncio.fixture
async def ingest_authors(
    client: AsyncClient, mock_github: GitHubMocker
) -> None:
    """Ingest lsst-texmf authors for testing."""
    mock_github.mock_lsst_texmf_ingest()

    response = await client.post(
        "/ook/ingest/lsst-texmf",
        json={"ingest_authordb": True, "ingest_glossary": False},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_author_alias_lifecycle(
    client: AsyncClient, ingest_authors: None
) -> None:
    """Test creating, resolving, listing, and deleting an alias.

    The authordb.yaml test dataset contains both ``marshallp`` and
    ``marshallpj`` as authors, so creating the alias also exercises the
    merge of the existing ``marshallpj`` author record.
    """
    # Both IDs exist as distinct authors after ingest
    response = await client.get("/ook/authors/marshallpj")
    assert response.status_code == 200
    assert response.json()["internal_id"] == "marshallpj"

    # Create the alias, merging the marshallpj record into marshallp
    response = await client.post(
        "/ook/admin/authors/aliases",
        json={
            "alias": "marshallpj",
            "canonical": "marshallp",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["alias"] == "marshallpj"
    assert data["canonical"] == "marshallp"

    # The alias resolves to the root author's record
    response = await client.get("/ook/authors/marshallpj")
    assert response.status_code == 200
    data = response.json()
    assert data["internal_id"] == "marshallp"
    assert data["given_name"] == "Phil"

    # The alias appears in the admin listing
    response = await client.get("/ook/admin/authors/aliases")
    assert response.status_code == 200
    assert {
        "alias": "marshallpj",
        "canonical": "marshallp",
    } in response.json()

    # The merged author no longer appears in search results
    response = await client.get(
        "/ook/authors", params={"search": "Marshall, P"}
    )
    assert response.status_code == 200
    internal_ids = [a["internal_id"] for a in response.json()]
    assert "marshallp" in internal_ids
    assert "marshallpj" not in internal_ids

    # Re-ingesting authordb.yaml skips the aliased ID instead of
    # recreating it (or failing on a duplicate ORCID)
    response = await client.post(
        "/ook/ingest/lsst-texmf",
        json={"ingest_authordb": True, "ingest_glossary": False},
    )
    assert response.status_code == 200
    response = await client.get("/ook/authors/marshallpj")
    assert response.status_code == 200
    assert response.json()["internal_id"] == "marshallp"

    # Delete the alias
    response = await client.delete("/ook/admin/authors/aliases/marshallpj")
    assert response.status_code == 204
    response = await client.get("/ook/authors/marshallpj")
    assert response.status_code == 404

    # Deleting again returns 404
    response = await client.delete("/ook/admin/authors/aliases/marshallpj")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_alias_root_author_not_found(
    client: AsyncClient, ingest_authors: None
) -> None:
    """Test that aliasing to a nonexistent author returns 404."""
    response = await client.post(
        "/ook/admin/authors/aliases",
        json={
            "alias": "somealias",
            "canonical": "doesnotexist",
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_alias_conflicts(
    client: AsyncClient, ingest_authors: None
) -> None:
    """Test conflicting alias creation requests."""
    # An alias can't be the same as the canonical author ID
    response = await client.post(
        "/ook/admin/authors/aliases",
        json={"alias": "sickj", "canonical": "sickj"},
    )
    assert response.status_code == 409

    # An existing alias can't be re-pointed to a different author
    response = await client.post(
        "/ook/admin/authors/aliases",
        json={
            "alias": "marshallpj",
            "canonical": "marshallp",
        },
    )
    assert response.status_code == 201
    response = await client.post(
        "/ook/admin/authors/aliases",
        json={"alias": "marshallpj", "canonical": "sickj"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_create_alias_chain_flattens(
    client: AsyncClient, ingest_authors: None
) -> None:
    """Test that aliasing to an alias resolves to the root author."""
    response = await client.post(
        "/ook/admin/authors/aliases",
        json={
            "alias": "marshallpj",
            "canonical": "marshallp",
        },
    )
    assert response.status_code == 201

    # Aliasing to marshallpj (an alias) points the new alias at marshallp
    response = await client.post(
        "/ook/admin/authors/aliases",
        json={
            "alias": "marshallphil",
            "canonical": "marshallpj",
        },
    )
    assert response.status_code == 201
    assert response.json()["canonical"] == "marshallp"


@pytest.mark.asyncio
async def test_delete_author_via_alias_conflicts(
    client: AsyncClient, ingest_authors: None
) -> None:
    """Test that deleting an author through an alias ID is rejected."""
    response = await client.post(
        "/ook/admin/authors/aliases",
        json={
            "alias": "marshallpj",
            "canonical": "marshallp",
        },
    )
    assert response.status_code == 201

    response = await client.delete("/ook/admin/authors/marshallpj")
    assert response.status_code == 409

    # The root author is untouched
    response = await client.get("/ook/authors/marshallp")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_alias_merge_repoints_contributors(
    client: AsyncClient, ingest_authors: None
) -> None:
    """Test that merging an author re-points document contributors and that
    new ingests resolve the alias.
    """
    document_data = {
        "documents": [
            {
                "title": "A Sample Technical Note",
                "url": "https://example.org/documents/sample-001",
                "date_resource_published": "2024-01-15T10:00:00Z",
                "date_resource_updated": "2024-01-20T15:30:00Z",
                "type": "Report",
                "series": "TEST",
                "handle": "TEST-001",
                "number": 1,
                "contributors": [
                    {
                        "author": {
                            "internal_id": "marshallpj",
                            "surname": "Marshall",
                            "given_name": "Philip J.",
                        },
                        "role": "Creator",
                        "order": 1,
                    },
                ],
            }
        ]
    }
    response = await client.post(
        "/ook/ingest/resources/documents", json=document_data
    )
    assert response.status_code == 200
    ingest_results = response.json()
    assert ingest_results[0]["status"] == "created"
    document_id = ingest_results[0]["resource"]["id"]

    # The document is attributed to the marshallpj author record
    response = await client.get(f"/ook/resources/{document_id}")
    assert response.status_code == 200
    creators = response.json()["creators"]
    assert len(creators) == 1
    assert creators[0]["internal_id"] == "marshallpj"

    # Create the alias, which merges marshallpj into marshallp
    response = await client.post(
        "/ook/admin/authors/aliases",
        json={
            "alias": "marshallpj",
            "canonical": "marshallp",
        },
    )
    assert response.status_code == 201

    # The document's attribution was re-pointed to the root author
    response = await client.get(f"/ook/resources/{document_id}")
    assert response.status_code == 200
    creators = response.json()["creators"]
    assert len(creators) == 1
    assert creators[0]["internal_id"] == "marshallp"
    assert creators[0]["given_name"] == "Phil"

    # A new document referencing the alias is attributed to the root author
    document_data["documents"][0]["handle"] = "TEST-002"
    document_data["documents"][0]["number"] = 2
    document_data["documents"][0]["url"] = (
        "https://example.org/documents/sample-002"
    )
    response = await client.post(
        "/ook/ingest/resources/documents", json=document_data
    )
    assert response.status_code == 200
    ingest_results = response.json()
    assert ingest_results[0]["status"] == "created"
    document_id = ingest_results[0]["resource"]["id"]

    response = await client.get(f"/ook/resources/{document_id}")
    assert response.status_code == 200
    creators = response.json()["creators"]
    assert len(creators) == 1
    assert creators[0]["internal_id"] == "marshallp"
