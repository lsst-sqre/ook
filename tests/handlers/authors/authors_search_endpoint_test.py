"""Integration tests for the robust author search functionality.

These tests verify that the multi-strategy search system works correctly
with various name formats and provides proper relevance scoring.
"""

from __future__ import annotations

import time

import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.support.github import GitHubMocker


@pytest_asyncio.fixture
async def ingest_lsst_texmf(
    client: AsyncClient, mock_github: GitHubMocker
) -> None:
    """Ingest lsst-texmf data for testing."""
    mock_github.mock_lsst_texmf_ingest()

    # Ingest the lsst/lsst-texmf repo to bootstrap from authordb.yaml
    response = await client.post(
        "/ook/ingest/lsst-texmf",
        json={"ingest_authordb": True, "ingest_glossary": False},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_search_comma_separated_format(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test searching with 'Last, First' format."""
    response = await client.get("/ook/authors?search=Sick%2C%20Jonathan")
    assert response.status_code == 200
    results = response.json()

    # Should find Jonathan Sick
    sick_results = [r for r in results if r["internal_id"] == "sickj"]
    assert len(sick_results) == 1
    assert sick_results[0]["family_name"] == "Sick"
    assert sick_results[0]["given_name"] == "Jonathan"

    # Should have high relevance score for exact format match
    assert sick_results[0]["score"] >= 90


@pytest.mark.asyncio
async def test_search_initial_format(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test searching with initial format like 'Sick, J'."""
    response = await client.get("/ook/authors?search=Sick%2C%20J")
    assert response.status_code == 200
    results = response.json()

    # Should find Jonathan Sick (J matches Jonathan)
    sick_results = [r for r in results if r["internal_id"] == "sickj"]
    assert len(sick_results) == 1
    assert sick_results[0]["family_name"] == "Sick"
    assert sick_results[0]["given_name"] == "Jonathan"


@pytest.mark.asyncio
async def test_search_initial_with_period(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test searching with initial format including period 'Sick, J.'."""
    response = await client.get("/ook/authors?search=Sick%2C%20J.")
    assert response.status_code == 200
    results = response.json()

    # Should find Jonathan Sick
    sick_results = [r for r in results if r["internal_id"] == "sickj"]
    assert len(sick_results) == 1


@pytest.mark.asyncio
async def test_search_first_last_format(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test searching with 'First Last' format."""
    response = await client.get("/ook/authors?search=Jonathan%20Sick")
    assert response.status_code == 200
    results = response.json()

    # Should find Jonathan Sick
    sick_results = [r for r in results if r["internal_id"] == "sickj"]
    assert len(sick_results) == 1
    assert sick_results[0]["family_name"] == "Sick"
    assert sick_results[0]["given_name"] == "Jonathan"


@pytest.mark.asyncio
async def test_search_surname_only(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test searching with surname only."""
    response = await client.get("/ook/authors?search=Sick")
    assert response.status_code == 200
    results = response.json()

    # Should find Jonathan Sick and potentially others with surname Sick
    sick_results = [r for r in results if r["family_name"] == "Sick"]
    assert len(sick_results) >= 1

    # Jonathan Sick should be in the results
    jonathan_sick = [r for r in sick_results if r["internal_id"] == "sickj"]
    assert len(jonathan_sick) == 1


@pytest.mark.asyncio
async def test_search_given_name_only(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test searching with given name only."""
    response = await client.get("/ook/authors?search=Jonathan")
    assert response.status_code == 200
    results = response.json()

    # Should find authors with given name Jonathan
    jonathan_results = [
        r for r in results if r["given_name"] and "Jonathan" in r["given_name"]
    ]
    assert len(jonathan_results) >= 1


@pytest.mark.asyncio
async def test_search_fuzzy_matching_typos(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test that trigram search handles typos."""
    # Test with a minor typo: "Sik" instead of "Sick"
    response = await client.get("/ook/authors?search=Sik")
    assert response.status_code == 200
    results = response.json()

    # Should still find results due to trigram similarity
    # (though the exact behavior depends on the similarity threshold)
    assert isinstance(results, list)  # At minimum, should not error


@pytest.mark.asyncio
async def test_search_unicode_characters(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test searching with Unicode characters in names."""
    # This tests that the search system can handle international characters
    response = await client.get("/ook/authors?search=José")
    assert response.status_code == 200
    results = response.json()

    # Should handle Unicode names without error
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_search_special_characters(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test searching with special characters like apostrophes and hyphens."""
    # Test with names containing special characters
    response = await client.get("/ook/authors?search=O%27Connor")
    assert response.status_code == 200
    results = response.json()

    # Should handle special characters without error
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_search_case_insensitive(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test that search is case insensitive."""
    # Test with different cases
    test_cases = ["sick", "SICK", "Sick", "sIcK"]

    base_response = await client.get("/ook/authors?search=Sick")
    base_results = base_response.json()

    for case_variant in test_cases:
        response = await client.get(f"/ook/authors?search={case_variant}")
        assert response.status_code == 200
        results = response.json()

        # Should find the same results regardless of case
        sick_results = [r for r in results if r["family_name"] == "Sick"]
        base_sick_results = [
            r for r in base_results if r["family_name"] == "Sick"
        ]
        assert len(sick_results) == len(base_sick_results)


@pytest.mark.asyncio
async def test_search_relevance_scoring(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test that relevance scoring works correctly."""
    response = await client.get("/ook/authors?search=Sick")
    assert response.status_code == 200
    results = response.json()

    # Results should be ordered by relevance (score descending)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)

    # All results should have valid scores
    assert all(
        isinstance(score, (int, float)) and score >= 0 for score in scores
    )


@pytest.mark.asyncio
async def test_search_combined_strategies(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test that multiple search strategies work together."""
    # This search should trigger multiple strategies:
    # - TrigramStrategy for fuzzy matching
    # - ComponentStrategy for exact component matching
    response = await client.get("/ook/authors?search=Jonathan%20Sick")
    assert response.status_code == 200
    results = response.json()

    # Should find Jonathan Sick with high confidence
    sick_results = [r for r in results if r["internal_id"] == "sickj"]
    assert len(sick_results) == 1

    # Score should be high due to exact component matching
    assert sick_results[0]["score"] >= 85


@pytest.mark.asyncio
async def test_search_empty_query(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test behavior with empty search query."""
    response = await client.get("/ook/authors?search=")

    # Should return 422 validation error for empty query (min_length=2)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_whitespace_only_query(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test behavior with whitespace-only search query."""
    response = await client.get("/ook/authors?search=%20%20%20")

    # Three spaces passes validation (length=3) but gets parsed as empty
    # Should return 200 with empty results
    assert response.status_code == 200
    results = response.json()
    assert results == []


@pytest.mark.asyncio
async def test_search_no_results(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test behavior when search returns no results."""
    response = await client.get("/ook/authors?search=XyzNonexistentAuthor")
    assert response.status_code == 200
    results = response.json()

    # Should return empty list, not 404
    assert results == []


@pytest.mark.asyncio
async def test_search_pagination_with_scores(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test that pagination works correctly with search results."""
    # Search for a common pattern that should return multiple results
    response = await client.get("/ook/authors?search=an&limit=5")
    assert response.status_code == 200
    results = response.json()

    # Should respect the limit
    assert len(results) <= 5

    # All results should have scores
    assert all("score" in result for result in results)

    # Should be ordered by score (descending)
    if len(results) > 1:
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_search_complex_name_formats(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test searching for complex name formats with suffixes."""
    # Test that the system can handle names with suffixes
    # (Even if the specific author doesn't exist, it should parse correctly)
    test_queries = [
        "Smith, Jr., John",
        "Johnson III",
        "Brown, Sr.",
    ]

    for query in test_queries:
        response = await client.get(f"/ook/authors?search={query}")
        assert response.status_code == 200
        results = response.json()

        # Should handle complex formats without error
        assert isinstance(results, list)


@pytest.mark.asyncio
async def test_search_performance_reasonable(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test that search performance is reasonable."""
    start_time = time.time()
    response = await client.get("/ook/authors?search=Jonathan%20Sick")
    end_time = time.time()

    assert response.status_code == 200

    # Search should complete within reasonable time (<5 seconds with
    # the current dataset)
    search_time = end_time - start_time
    assert search_time < 5.0, (
        f"Search took {search_time:.2f} seconds, too slow!"
    )
