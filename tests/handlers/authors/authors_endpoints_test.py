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

    # Ingest the lsst/lsst-texmf repo to bootstrap from authordb.yaml
    response = await client.post(
        "/ook/ingest/lsst-texmf",
        json={"ingest_authordb": True, "ingest_glossary": False},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_author_by_id(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test the /authors/{internal_id} endpoint.

    This fixture sets up the test data and yields the response data.
    """
    # Get author by internal ID
    response = await client.get("/ook/authors/sickj")
    assert response.status_code == 200
    data = response.json()
    assert data["internal_id"] == "sickj"
    assert data["family_name"] == "Sick"
    assert data["given_name"] == "Jonathan"
    assert data["orcid"] == "https://orcid.org/0000-0003-3001-676X"
    assert "email" not in data
    assert data["affiliations"][0]["name"] == "J.Sick Codes Inc."
    assert (
        data["affiliations"][1]["name"]
        == "Vera C. Rubin Observatory Project Office"
    )
    assert data["affiliations"][1]["ror"] == "https://ror.org/048g3cy84"


@pytest.mark.asyncio
async def test_get_author_by_id_not_found(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test the /authors/{internal_id} endpoint with a non-existent ID."""
    response = await client.get("/ook/authors/doesnotexist")
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


@pytest.mark.asyncio
async def test_search_authors_family_name(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test searching authors by family name."""
    response = await client.get("/ook/authors?search=Sick")
    assert response.status_code == 200
    results = response.json()
    assert len(results) >= 1
    assert all("score" in result for result in results)
    # Should find Jonathan Sick with high relevance
    sick_results = [r for r in results if r["internal_id"] == "sickj"]
    assert len(sick_results) == 1
    assert sick_results[0]["family_name"] == "Sick"


@pytest.mark.asyncio
async def test_search_authors_full_name(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test searching authors by full name."""
    response = await client.get("/ook/authors?search=Jonathan%20Sick")
    assert response.status_code == 200
    results = response.json()
    assert len(results) >= 1
    assert all("score" in result for result in results)
    # Should find Jonathan Sick with high relevance
    sick_results = [r for r in results if r["internal_id"] == "sickj"]
    assert len(sick_results) == 1
    assert sick_results[0]["family_name"] == "Sick"


@pytest.mark.asyncio
async def test_search_authors_partial_family_name(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test searching authors with partial family name."""
    response = await client.get("/ook/authors?search=Gold")
    assert response.status_code == 200
    results = response.json()
    print("Search results:", results)
    assert len(results) >= 1
    assert all("score" in result for result in results)
    # Should find internal_id goldinat and goldsteinda with high relevance
    gold_results = [
        r for r in results if r["internal_id"] in ["goldinat", "goldsteinda"]
    ]
    assert len(gold_results) == 2


@pytest.mark.asyncio
async def test_search_authors_relevance_ordering(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test that search results are ordered by relevance score."""
    # Search for a common letter that should match many authors
    response = await client.get("/ook/authors?search=Ho")
    assert response.status_code == 200
    results = response.json()
    assert len(results) >= 2

    # Verify all results have scores
    assert all("score" in result for result in results)

    # Verify results are ordered by score (descending)
    scores = [result["score"] for result in results]
    assert scores == sorted(scores, reverse=True)

    # Verify scores are within valid range
    assert all(0 <= score <= 100 for score in scores)

    # Check if pagination headers are present when there are more results
    if "Link" in response.headers:
        links = PaginationLinkData.from_header(response.headers["Link"])
        if links.next_url:
            # Test next page
            next_response = await client.get(links.next_url)
            assert next_response.status_code == 200
            next_results = next_response.json()
            assert all("score" in result for result in next_results)

            # Results should be different (no duplicates across pages)
            first_page_ids = {r["internal_id"] for r in results}
            next_page_ids = {r["internal_id"] for r in next_results}
            assert first_page_ids.isdisjoint(next_page_ids)


@pytest.mark.asyncio
async def test_search_authors_no_results(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test search with no matching results."""
    # Search for something very unlikely to match
    response = await client.get("/ook/authors?search=xyznonexistent")
    assert response.status_code == 200  # Empty results return 200
    results = response.json()
    assert results == []  # Empty list for no results
    assert response.headers["X-Total-Count"] == "0"


@pytest.mark.asyncio
async def test_search_authors_empty_query(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test behavior with empty search query."""
    # Empty search should fail validation due to min_length=2
    response = await client.get("/ook/authors?search=")
    assert response.status_code == 422  # Validation error

    # Short search (1 char) should also fail validation
    response = await client.get("/ook/authors?search=a")
    assert response.status_code == 422  # Validation error

    # But no search parameter should work (regular author listing)
    response = await client.get("/ook/authors")
    assert response.status_code == 200
    results = response.json()
    assert len(results) >= 1
    # Regular listing should not have scores
    assert all("score" not in result for result in results)
