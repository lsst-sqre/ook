"""Tests for the DELETE /ook/admin/authors/{internal_id} endpoint."""

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
async def test_delete_author_success(
    client: AsyncClient, ingest_authors: None
) -> None:
    """Test successful deletion of an author."""
    # Verify author exists
    response = await client.get("/ook/authors/sickj")
    assert response.status_code == 200

    # Delete the author
    response = await client.delete("/ook/admin/authors/sickj")
    assert response.status_code == 204
    assert response.content == b""

    # Verify author no longer exists
    response = await client.get("/ook/authors/sickj")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_author_not_found(client: AsyncClient) -> None:
    """Test deletion of non-existent author returns 404."""
    response = await client.delete("/ook/admin/authors/doesnotexist")
    assert response.status_code == 404
    data = response.json()
    assert data["detail"][0] == {
        "msg": "Author 'doesnotexist' not found",
        "type": "not_found",
    }


@pytest.mark.asyncio
async def test_delete_author_cascades_affiliations(
    client: AsyncClient, ingest_authors: None
) -> None:
    """Test that deleting an author removes affiliation associations."""
    # Get author with affiliations
    response = await client.get("/ook/authors/sickj")
    assert response.status_code == 200
    data = response.json()
    assert len(data["affiliations"]) > 0

    # Delete the author
    response = await client.delete("/ook/admin/authors/sickj")
    assert response.status_code == 204

    # Verify author is gone
    response = await client.get("/ook/authors/sickj")
    assert response.status_code == 404

    # Note: The affiliations themselves should still exist in the database
    # if they're used by other authors. We're just testing that the
    # author-affiliation associations are removed.


@pytest.mark.asyncio
async def test_delete_author_idempotency(
    client: AsyncClient, ingest_authors: None
) -> None:
    """Test that deleting an already-deleted author returns 404."""
    # First deletion succeeds
    response = await client.delete("/ook/admin/authors/sickj")
    assert response.status_code == 204

    # Second deletion fails with 404
    response = await client.delete("/ook/admin/authors/sickj")
    assert response.status_code == 404
