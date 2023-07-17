"""Tests for the example.handlers.external module and routes."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from ook.config import config


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
async def test_post_ingest_ltd(client: AsyncClient) -> None:
    """Test ``POST /ook/ingest/ltd``."""
    request_data = {"product_slug": "sqr-000"}
    response = await client.post("/ook/ingest/ltd", json=request_data)
    assert response.status_code == 501
