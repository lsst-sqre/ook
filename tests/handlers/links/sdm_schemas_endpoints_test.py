"""Tests for the /ook/links/domains/sdm-schemas/ endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.support.github import GitHubMocker


@pytest.mark.asyncio
async def test_sdm_domain_info(
    client: AsyncClient, mock_github: GitHubMocker
) -> None:
    """Test ``/ook/links/domains/sdm`` info endpoint."""
    # Get info about the SDM domain
    response = await client.get("/ook/links/domains/sdm")
    assert response.status_code == 200
    data = response.json()
    assert "entities" in data
    assert "collections" in data


@pytest.mark.asyncio
async def test_sdm_schemas_links(
    client: AsyncClient, mock_github: GitHubMocker
) -> None:
    """Test ``/ook/links/domains/sdm`` endpoints."""
    mock_github.mock_sdm_schema_release_ingest()

    # Ingest the SDM schemas
    response = await client.post(
        "/ook/ingest/sdm-schemas", json={"github_release_tag": None}
    )
    assert response.status_code == 200

    # Get links to column docs for the dp02_dc2_catalogs schema's Visit table
    response = await client.get(
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables/Visit/columns"
    )
    assert response.status_code == 200
    data = response.json()
    # Should be 15 columns in the Visit table
    assert len(data) == 15
    # Look at shape of the first column, which should be the visit column
    # as ordered by tap index
    assert data[0]["entity"]["column_name"] == "visit"
    assert data[0]["entity"]["table_name"] == "Visit"
    assert data[0]["entity"]["schema_name"] == "dp02_dc2_catalogs"
    assert data[0]["entity"]["domain"] == "sdm"
    assert data[0]["entity"]["domain_type"] == "column"
    assert data[0]["entity"]["self_url"].endswith(
        "/tables/Visit/columns/visit"
    )
    assert data[0]["links"][0]["url"].endswith("#Visit.visit")

    # Get links_to tables' docs for the dp02_dc2_catalogs schema
    response = await client.get(
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables"
    )
    assert response.status_code == 200
    data = response.json()
    # Should be 11 tables in the dp02_dc2_catalogs schema
    assert len(data) == 11
    # Check that the tables are ordered by tap index (first should be Object)
    assert data[0]["entity"]["table_name"] == "Object"
    # Check that all entities are tables
    assert all(entity["entity"]["domain_type"] == "table" for entity in data)

    # Get links to tables' and columns' docs for the dp02_dc2_catalogs schema
    response = await client.get(
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables?include_columns=true"
    )
    assert response.status_code == 200
    data = response.json()
    # Check that there are both tables and columns
    assert any(entity["entity"]["domain_type"] == "table" for entity in data)
    assert any(entity["entity"]["domain_type"] == "column" for entity in data)

    # Get links to all schemas
    response = await client.get("/ook/links/domains/sdm/schemas")
    assert response.status_code == 200
    data = response.json()
    # should be 2 schemas with links
    assert len(data) == 2
    # Check that all entities are schemas
    assert all(entity["entity"]["domain_type"] == "schema" for entity in data)
