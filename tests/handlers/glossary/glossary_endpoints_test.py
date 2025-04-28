"""Tests for the /ook/glossary endpoints."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.support.github import GitHubMocker


@pytest_asyncio.fixture
async def ingest_lsst_texmf(
    client: AsyncClient, mock_github: GitHubMocker
) -> None:
    """Ingest lsst-texmf data for testing.

    This fixture mocks the GitHub API and ingests lsst/lsst-texmf from
    the internal datasets.
    """
    mock_github.mock_lsst_texmf_ingest()

    # Ingest the lsst/lsst-texmf repo (authordb.yaml and glossary)
    response = await client.post("/ook/ingest/lsst-texmf", json={})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_search(client: AsyncClient, ingest_lsst_texmf: None) -> None:
    """Test the /glossary/search endpoint."""
    response = await client.get("/ook/glossary/search", params={"q": "square"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["term"] == "SQuaRE"
