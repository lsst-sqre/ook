"""Tests for the /ook/links/domains/sdm-schemas/ endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.support.github import GitHubMocker


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
    # Check that the columns are ordered by tap index
    assert data[0]["column_name"] == "visit"
    assert data[0]["links"][0]["url"].endswith("#Visit.visit")
    assert data[0]["self_url"].endswith("/tables/Visit/columns/visit")
