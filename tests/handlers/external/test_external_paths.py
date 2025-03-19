"""Tests for the example.handlers.external module and routes."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from ook.config import config
from tests.support.github import GitHubMocker


@pytest.mark.asyncio
async def test_get_index(client: AsyncClient) -> None:
    """Test ``GET /ook/``."""
    response = await client.get("/ook/")
    assert response.status_code == 200
    data = response.json()
    metadata = data["metadata"]
    assert metadata["name"] == config.name
    assert isinstance(metadata["version"], str)
    assert isinstance(metadata["description"], str)
    assert isinstance(metadata["repository_url"], str)
    assert isinstance(metadata["documentation_url"], str)


@pytest.mark.asyncio
async def test_post_ingest_sdm_schemas(
    client: AsyncClient, mock_github: GitHubMocker
) -> None:
    """Test ``POST /ook/ingest/sdm-schemas``."""
    mock_github.mock_sdm_schema_release_ingest()
    response = await client.post("/ook/ingest/sdm-schemas")
    assert response.status_code == 200

    response = await client.get(
        "/ook/links/domains/sdm-schemas/schemas/dp02_dc2_catalogs"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["links"][0] == {
        "url": "https://sdm-schemas.lsst.io/dp02.html",
        "kind": "schema browser",
        "source_title": "Science Data Model Schemas",
    }
    assert data["name"] == "dp02_dc2_catalogs"
    assert data["self_url"].endswith(
        "/ook/links/domains/sdm-schemas/schemas/dp02_dc2_catalogs"
    )

    response = await client.get("/ook/links/domains/sdm-schemas/schemas")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["name"] == "dp02_dc2_catalogs"
    assert data[1]["name"] == "dp03_catalogs_10yr"
