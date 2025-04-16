"""Tests for the /ook/links/domains/sdm-schemas/ endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from safir.database import PaginationLinkData

from tests.support.github import GitHubMocker


@pytest_asyncio.fixture
async def ingest_sdm_schemas(
    client: AsyncClient, mock_github: GitHubMocker
) -> None:
    """Ingest SDM schemas for testing.

    This fixture mocks the GitHub API and ingests SDM schemas.
    """
    mock_github.mock_sdm_schema_release_ingest()

    # Ingest the SDM schemas
    response = await client.post(
        "/ook/ingest/sdm-schemas", json={"github_release_tag": None}
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_sdm_domain_info(client: AsyncClient) -> None:
    """Test ``/ook/links/domains/sdm`` info endpoint."""
    # Get info about the SDM domain
    response = await client.get("/ook/links/domains/sdm")
    assert response.status_code == 200
    data = response.json()
    assert "entities" in data
    assert "collections" in data


@pytest.mark.asyncio
async def test_sdm_schema_endpoints(
    client: AsyncClient, ingest_sdm_schemas: None
) -> None:
    """Test all SDM schema endpoints.

    This is a single entrypoint that calls individual test functions to
    help with test organization while using the function-scoped client fixture.
    """
    await _test_sdm_schema_table_columns(client)
    await _test_sdm_schema_tables(client)
    await _test_sdm_schema_tables_with_columns(client)
    await _test_sdm_schemas(client)
    await _test_sdm_schemas_all_entities(client)


async def _test_sdm_schema_table_columns(client: AsyncClient) -> None:
    """Test endpoint for column docs for a specific table."""
    # Get links to column docs for the dp02_dc2_catalogs schema's Visit table
    response = await client.get(
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables/Visit/columns"
    )
    assert response.status_code == 200
    data = response.json()
    # Should be 15 columns in the Visit table
    assert len(data) == 15
    # Look at shape of the first column, which should be the visit column
    # as ordered by tap index
    assert data[0]["entity"]["column_name"] == "visit"
    assert data[0]["entity"]["table_name"] == "Visit"
    assert data[0]["entity"]["schema_name"] == "dp02_dc2_catalogs"
    assert data[0]["entity"]["domain"] == "sdm"
    assert data[0]["entity"]["domain_type"] == "column"
    assert data[0]["entity"]["self_url"].endswith(
        "/tables/Visit/columns/visit"
    )
    assert data[0]["links"][0]["url"].endswith("#Visit.visit")


async def _test_sdm_schema_tables(client: AsyncClient) -> None:
    """Test endpoint for tables in a specific schema."""
    # Get links to tables' docs for the dp02_dc2_catalogs schema
    response = await client.get(
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables"
    )
    assert response.status_code == 200
    data = response.json()
    # Should be 11 tables in the dp02_dc2_catalogs schema
    assert len(data) == 11
    # Check that the tables are ordered by tap index (first should be Object)
    assert data[0]["entity"]["table_name"] == "Object"
    # Check that all entities are tables
    assert all(entity["entity"]["domain_type"] == "table" for entity in data)


async def _test_sdm_schema_tables_with_columns(client: AsyncClient) -> None:
    """Test endpoint for tables and columns in a specific schema."""
    # Get links to tables' and columns' docs for the dp02_dc2_catalogs schema
    url = (
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables"
        "?include_columns=true&limit=10"
    )
    response = await client.get(url)
    assert response.status_code == 200
    count = int(response.headers["X-Total-Count"])

    data: list[dict[str, Any]] = []
    async for page in get_iter(client, url):
        data.extend(page)
    # Check that there are both tables and columns
    assert any(entity["entity"]["domain_type"] == "table" for entity in data)
    assert any(entity["entity"]["domain_type"] == "column" for entity in data)
    # Check that the number of entities is correct after pagination
    assert len(data) == count


async def _test_sdm_schemas(client: AsyncClient) -> None:
    """Test endpoint for all schemas."""
    # Get links to all schemas
    response = await client.get("/ook/links/domains/sdm/schemas")
    assert response.status_code == 200
    data = response.json()
    # Check that all entities are schemas
    assert all(entity["entity"]["domain_type"] == "schema" for entity in data)
    # should be 2 schemas with links
    assert len(data) == 2


async def _test_sdm_schemas_all_entities(client: AsyncClient) -> None:
    """Test /ook/links/domains/sdm/schemas with columns and tables included."""
    url = (
        "/ook/links/domains/sdm/schemas"
        "?include_tables=true&include_columns=true"
    )
    r = await client.get(url)
    assert r.status_code == 200
    count = int(r.headers["X-Total-Count"])

    # Get links to all schemas
    data: list[dict[str, Any]] = []
    async for page in get_iter(client, url):
        data.extend(page)

    # Check that there are schemas, tables, and columns
    assert any(entity["entity"]["domain_type"] == "schema" for entity in data)
    assert any(entity["entity"]["domain_type"] == "table" for entity in data)
    assert any(entity["entity"]["domain_type"] == "column" for entity in data)
    # Check that the number of entities is correct after pagination
    assert len(data) == count


async def get_iter(client: AsyncClient, url: str) -> AsyncIterator[list[Any]]:
    """Iterate over pages of links."""
    while url:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
        yield data
        links = PaginationLinkData.from_header(response.headers["Link"])
        if links.next_url is None:
            break
        url = links.next_url


@pytest.mark.asyncio
async def test_individual_schema_links(
    client: AsyncClient, ingest_sdm_schemas: None
) -> None:
    """Test endpoint for getting links for an individual schema."""
    # Test getting links for a specific schema
    response = await client.get(
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    # Check link structure
    assert "url" in data[0]
    assert "type" in data[0]
    assert "title" in data[0]


@pytest.mark.asyncio
async def test_individual_table_links(
    client: AsyncClient, ingest_sdm_schemas: None
) -> None:
    """Test endpoint for getting links for an individual table."""
    # Test getting links for a specific table
    response = await client.get(
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables/Visit"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    # Check link structure
    assert "url" in data[0]
    assert "type" in data[0]
    assert "title" in data[0]


@pytest.mark.asyncio
async def test_individual_column_links(
    client: AsyncClient, ingest_sdm_schemas: None
) -> None:
    """Test endpoint for getting links for an individual column."""
    # Test getting links for a specific column
    response = await client.get(
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables/Visit/columns/visit"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    # Check link structure
    assert "url" in data[0]
    assert "type" in data[0]
    assert "title" in data[0]


@pytest.mark.asyncio
async def test_error_cases(
    client: AsyncClient, ingest_sdm_schemas: None
) -> None:
    """Test error responses for non-existent entities."""
    # Test non-existent schema
    response = await client.get(
        "/ook/links/domains/sdm/schemas/non_existent_schema"
    )
    assert response.status_code == 404

    # Test non-existent table
    response = await client.get(
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables/NonExistentTable"
    )
    assert response.status_code == 404

    # Test non-existent column
    response = await client.get(
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables/Visit/columns/non_existent_column"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_pagination_parameters(
    client: AsyncClient, ingest_sdm_schemas: None
) -> None:
    """Test pagination parameters for endpoints that support them."""
    # Test with custom limit
    response = await client.get(
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables/Visit/columns?limit=5"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 5
    assert "Link" in response.headers
    assert "X-Total-Count" in response.headers

    # Test with cursor (using the Link header from the previous response)
    links = PaginationLinkData.from_header(response.headers["Link"])
    assert links.next_url is not None

    # Follow the next link
    response2 = await client.get(links.next_url)
    assert response2.status_code == 200
    data2 = response2.json()
    assert len(data2) > 0
    # Make sure we got different data
    assert (
        data[0]["entity"]["column_name"] != data2[0]["entity"]["column_name"]
    )


@pytest.mark.asyncio
async def test_sdm_schema_ingest_idempotency(
    client: AsyncClient, ingest_sdm_schemas: None
) -> None:
    """Test the endpoint for schema link info remains consistent after
    ingests.
    """
    # First get the initial state
    response1 = await client.get("/ook/links/domains/sdm/schemas")
    assert response1.status_code == 200

    # Re-ingest the schemas (which should be idempotent)
    response_ingest = await client.post(
        "/ook/ingest/sdm-schemas", json={"github_release_tag": None}
    )
    assert response_ingest.status_code == 200

    # Check that the schemas are still accessible
    response2 = await client.get("/ook/links/domains/sdm/schemas")
    assert response2.status_code == 200
    # The data should be the same
    assert response1.json() == response2.json()


@pytest.mark.asyncio
async def test_with_minimum_limit(
    client: AsyncClient, ingest_sdm_schemas: None
) -> None:
    """Test with the smallest possible limit (1)."""
    response = await client.get(
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables/Visit/columns"
        "?limit=1"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert "Link" in response.headers
    assert "X-Total-Count" in response.headers
    total_count = int(response.headers["X-Total-Count"])
    assert total_count > 1  # Should be more than 1 column

    # Collect all pages to ensure we can get all items with pagination
    all_data: list[dict[str, Any]] = []
    url = (
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables/Visit/columns"
        "?limit=1"
    )
    async for page in get_iter(client, url):
        all_data.extend(page)

    # Verify we got all items
    assert len(all_data) == total_count

    # Verify no duplicates by checking column names
    column_names = [item["entity"]["column_name"] for item in all_data]
    assert len(column_names) == len(set(column_names))


@pytest.mark.asyncio
async def test_tables_and_columns_together(
    client: AsyncClient, ingest_sdm_schemas: None
) -> None:
    """Test using both include_tables and include_columns together."""
    url = (
        "/ook/links/domains/sdm/schemas"
        "?include_tables=true&include_columns=true&limit=20"
    )
    response = await client.get(url)
    assert response.status_code == 200
    data = response.json()

    # Verify we have all three entity types
    entity_types = [item["entity"]["domain_type"] for item in data]
    assert "schema" in entity_types
    assert "table" in entity_types
    assert "column" in entity_types

    # Check that entities maintain correct relationships
    for item in data:
        if item["entity"]["domain_type"] == "column":
            # Columns should have schema_name, table_name, and column_name
            assert item["entity"]["schema_name"]
            assert item["entity"]["table_name"]
            assert item["entity"]["column_name"]
        elif item["entity"]["domain_type"] == "table":
            # Tables should have schema_name and table_name
            assert item["entity"]["schema_name"]
            assert item["entity"]["table_name"]
            assert "column_name" not in item["entity"]
        elif item["entity"]["domain_type"] == "schema":
            # Schemas should have schema_name
            assert item["entity"]["schema_name"]
            assert "table_name" not in item["entity"]
            assert "column_name" not in item["entity"]


@pytest.mark.asyncio
async def test_multi_page_navigation(
    client: AsyncClient, ingest_sdm_schemas: None
) -> None:
    """Test navigating through multiple pages using cursor pagination."""
    # Start with a small limit to ensure multiple pages
    url = (
        "/ook/links/domains/sdm/schemas"
        "?include_tables=true&include_columns=true&limit=5"
    )

    # First page
    response = await client.get(url)
    assert response.status_code == 200
    first_page = response.json()
    assert len(first_page) == 5

    # Extract pagination links
    links = PaginationLinkData.from_header(response.headers["Link"])
    assert links.next_url is not None

    # Second page
    response2 = await client.get(links.next_url)
    assert response2.status_code == 200
    second_page = response2.json()
    assert len(second_page) == 5

    # Make sure pages don't overlap
    first_ids = [
        f"{item['entity']['schema_name']}:"
        f"{item['entity'].get('table_name', '')}"
        f":{item['entity'].get('column_name', '')}"
        for item in first_page
    ]
    second_ids = [
        f"{item['entity']['schema_name']}:"
        f"{item['entity'].get('table_name', '')}"
        f":{item['entity'].get('column_name', '')}"
        for item in second_page
    ]
    assert not set(first_ids).intersection(set(second_ids))

    # Extract links for next page
    links2 = PaginationLinkData.from_header(response2.headers["Link"])

    # Check that the previous link from the second page points to the first
    assert links2.prev_url is not None
    response_prev = await client.get(links2.prev_url)
    assert response_prev.status_code == 200
    prev_page = response_prev.json()

    # Verify we got back to the first page
    prev_ids = [
        f"{item['entity']['schema_name']}:"
        f"{item['entity'].get('table_name', '')}"
        f":{item['entity'].get('column_name', '')}"
        for item in prev_page
    ]
    assert set(first_ids) == set(prev_ids)


@pytest.mark.asyncio
async def test_table_links_pagination(
    client: AsyncClient, ingest_sdm_schemas: None
) -> None:
    """Test navigating through table links pages using
    SdmTableLinksCollectionCursor.
    """
    # Start with a small limit to ensure multiple pages
    url = "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables?limit=3"

    # First page
    response = await client.get(url)
    assert response.status_code == 200
    first_page = response.json()
    assert len(first_page) == 3

    # Extract pagination links
    links = PaginationLinkData.from_header(response.headers["Link"])
    assert links.next_url is not None

    # Second page
    response2 = await client.get(links.next_url)
    assert response2.status_code == 200
    second_page = response2.json()
    assert len(second_page) > 0

    # Make sure pages don't overlap
    first_ids = [
        f"{item['entity']['schema_name']}:{item['entity']['table_name']}"
        for item in first_page
    ]
    second_ids = [
        f"{item['entity']['schema_name']}:{item['entity']['table_name']}"
        for item in second_page
    ]
    assert not set(first_ids).intersection(set(second_ids))

    # Extract links for next page
    links2 = PaginationLinkData.from_header(response2.headers["Link"])

    # Check that the previous link from the second page points to the first
    assert links2.prev_url is not None
    response_prev = await client.get(links2.prev_url)
    assert response_prev.status_code == 200
    prev_page = response_prev.json()

    # Verify we got back to the first page
    prev_ids = [
        f"{item['entity']['schema_name']}:{item['entity']['table_name']}"
        for item in prev_page
    ]
    assert set(first_ids) == set(prev_ids)


@pytest.mark.asyncio
async def test_column_links_pagination(
    client: AsyncClient, ingest_sdm_schemas: None
) -> None:
    """Test navigating through column links pages using
    SdmColumnLinksCollectionCursor.
    """
    # CcdVisit has multiple columns, so it works well for pagination testing
    url = (
        "/ook/links/domains/sdm/schemas/dp02_dc2_catalogs/tables/CcdVisit/"
        "columns?limit=5"
    )

    # First page
    response = await client.get(url)
    assert response.status_code == 200
    first_page = response.json()
    assert len(first_page) == 5

    # Extract pagination links
    links = PaginationLinkData.from_header(response.headers["Link"])
    assert links.next_url is not None

    # Second page
    response2 = await client.get(links.next_url)
    assert response2.status_code == 200
    second_page = response2.json()
    assert len(second_page) > 0

    # Make sure pages don't overlap
    first_ids = [
        f"{item['entity']['schema_name']}"
        f":{item['entity']['table_name']}"
        f":{item['entity']['column_name']}"
        for item in first_page
    ]
    second_ids = [
        f"{item['entity']['schema_name']}"
        f":{item['entity']['table_name']}"
        f":{item['entity']['column_name']}"
        for item in second_page
    ]
    assert not set(first_ids).intersection(set(second_ids))

    # Extract links for next page
    links2 = PaginationLinkData.from_header(response2.headers["Link"])

    # Check that the previous link from the second page points to the first
    assert links2.prev_url is not None
    response_prev = await client.get(links2.prev_url)
    assert response_prev.status_code == 200
    prev_page = response_prev.json()

    # Verify we got back to the first page
    prev_ids = [
        f"{item['entity']['schema_name']}"
        f":{item['entity']['table_name']}"
        f":{item['entity']['column_name']}"
        for item in prev_page
    ]
    assert set(first_ids) == set(prev_ids)
