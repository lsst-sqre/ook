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
