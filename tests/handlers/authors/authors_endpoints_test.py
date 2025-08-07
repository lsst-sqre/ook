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

    # Check that affiliations include address data with formatted field
    for affiliation in data["affiliations"]:
        if affiliation.get("address"):
            assert "formatted" in affiliation["address"]
            # Formatted field can be None or a string
            formatted_addr = affiliation["address"]["formatted"]
            assert formatted_addr is None or isinstance(formatted_addr, str)


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
async def test_author_address_formatting_in_api_response(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test that formatted addresses appear correctly in API responses."""
    # Get an author with address data
    response = await client.get("/ook/authors")
    assert response.status_code == 200
    authors = response.json()

    # Look for an author with affiliations that have addresses
    author_with_address = None
    for author in authors:
        for affiliation in author.get("affiliations", []):
            if affiliation.get("address"):
                author_with_address = author
                break
        if author_with_address:
            break

    if author_with_address:
        # Verify the formatted field is present
        for affiliation in author_with_address["affiliations"]:
            address = affiliation.get("address")
            if address:
                # The formatted field should be present
                assert "formatted" in address

                # If there are address components, formatted should not be None
                has_components = any(
                    [
                        address.get("street"),
                        address.get("city"),
                        address.get("state"),
                        address.get("postal_code"),
                        address.get("country"),
                        address.get("country_code"),
                    ]
                )

                if has_components:
                    formatted = address["formatted"]
                    assert formatted is not None
                    assert isinstance(formatted, str)
                    assert len(formatted.strip()) > 0
                else:
                    # If no components, formatted can be None
                    assert address["formatted"] is None


@pytest.mark.asyncio
async def test_author_list_includes_formatted_addresses(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """Test that the authors list endpoint includes formatted addresses."""
    response = await client.get("/ook/authors?limit=5")
    assert response.status_code == 200
    authors = response.json()

    # Check that all authors maintain the formatted field structure
    for author in authors:
        for affiliation in author.get("affiliations", []):
            address = affiliation.get("address")
            if address:
                # The formatted field must be present (though can be None)
                assert "formatted" in address
                formatted = address.get("formatted")
                # If formatted is not None, it should be a string
                if formatted is not None:
                    assert isinstance(formatted, str)
