"""Tests for the /ook/authors endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from safir.database import PaginationLinkData

from tests.support.github import GitHubMocker


async def get_iter(client: AsyncClient, url: str) -> AsyncIterator[list[Any]]:
    """Iterate over pages of with keyset pagination."""
    while url:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
        yield data
        links = PaginationLinkData.from_header(response.headers["Link"])
        if links.next_url is None:
            break
        url = links.next_url


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
async def test_get_author_by_id(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test the /authors/id/{internal_id} endpoint.

    This fixture sets up the test data and yields the response data.
    """
    # Get author by internal ID
    response = await client.get("/ook/authors/id/sickj")
    assert response.status_code == 200
    data = response.json()
    assert data["internal_id"] == "sickj"
    assert data["surname"] == "Sick"
    assert data["given_name"] == "Jonathan"
    assert data["orcid"] == "https://orcid.org/0000-0003-3001-676X"
    assert "email" not in data
    assert data["affiliations"][0]["name"] == "J.Sick Codes Inc."
    assert (
        data["affiliations"][1]["name"]
        == "Vera C. Rubin Observatory Project Office"
    )


@pytest.mark.asyncio
async def test_get_author_by_id_not_found(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test the /authors/id/{internal_id} endpoint with a non-existent ID."""
    response = await client.get("/ook/authors/id/doesnotexist")
    assert response.status_code == 404
    data = response.json()
    assert data["detail"][0] == {
        "msg": "Author 'doesnotexist' not found",
        "type": "not_found",
    }


@pytest.mark.asyncio
async def test_get_authors(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test the /authors endpoint."""
    # Get all authors
    all_data: list[dict[str, Any]] = []
    async for page in get_iter(client, "/ook/authors"):
        all_data.extend(page)
    total_count = len(all_data)

    response = await client.get("/ook/authors?limit=10")
    assert response.status_code == 200
    assert int(response.headers["X-Total-Count"]) == total_count
    first_page = response.json()
    assert len(first_page) == 10
    links = PaginationLinkData.from_header(response.headers["Link"])

    assert links.next_url is not None
    response = await client.get(links.next_url)
    assert response.status_code == 200
    links = PaginationLinkData.from_header(response.headers["Link"])

    assert links.prev_url is not None
    response = await client.get(links.prev_url)
    assert response.status_code == 200
    first_page_again = response.json()
    assert first_page == first_page_again
