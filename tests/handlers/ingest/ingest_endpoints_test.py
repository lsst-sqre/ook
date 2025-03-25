"""Tests for the /ook/ingest endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

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
        "/ook/links/domains/sdm-schemas/schemas/dp02_dc2_catalogs"
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
        "/ook/links/domains/sdm-schemas/schemas/dp02_dc2_catalogs/tables/Object"
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
        "/ook/links/domains/sdm-schemas/schemas/dp02_dc2_catalogs/tables/Visit/columns/ra"
    )
    assert response.status_code == 200
    data = response.json()
    assert data[0] == {
        "url": "https://sdm-schemas.lsst.io/dp02.html#Visit.ra",
        "title": "ra column",
        "type": "schema_browser",
        "collection_title": "Science Data Model Schemas",
    }
