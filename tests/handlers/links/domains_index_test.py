"""Tests for the ``/ook/links/domains`` index endpoint."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from ook.config import config

DOMAINS_URL = f"{config.path_prefix}/links/domains"
"""The cross-domain index of the Links API."""


@pytest.mark.asyncio
async def test_domains_index_lists_every_domain(client: AsyncClient) -> None:
    """The index names every link domain Ook publishes."""
    response = await client.get(DOMAINS_URL)

    assert response.status_code == 200
    data = response.json()
    # Order is the order the domains are registered in, so a client that
    # renders the index gets a stable listing rather than one that shuffles
    # between requests.
    assert [domain["name"] for domain in data] == ["sdm", "python"]

    domains: dict[str, Any] = {domain["name"]: domain for domain in data}
    assert domains["sdm"]["self_url"].endswith("/ook/links/domains/sdm")
    assert domains["python"]["self_url"].endswith("/ook/links/domains/python")


@pytest.mark.asyncio
async def test_domains_index_carries_uri_templates(
    client: AsyncClient,
) -> None:
    """Each entry carries the domain's own entity and collection templates.

    A client can therefore address any domain's entities straight from the
    index, without a second request per domain.
    """
    response = await client.get(DOMAINS_URL)

    assert response.status_code == 200
    domains: dict[str, Any] = {
        domain["name"]: domain for domain in response.json()
    }

    sdm = domains["sdm"]
    assert sdm["entities"]["schema"].endswith(
        "/ook/links/domains/sdm/schemas/{schema_name}"
    )
    assert sdm["entities"]["table"].endswith(
        "/ook/links/domains/sdm/schemas/{schema_name}/tables/{table_name}"
    )
    assert sdm["entities"]["column"].endswith(
        "/ook/links/domains/sdm/schemas/{schema_name}"
        "/tables/{table_name}/columns/{column_name}"
    )
    assert sdm["collections"]["schemas"].endswith(
        "/ook/links/domains/sdm/schemas"
    )
    assert sdm["collections"]["tables"].endswith(
        "/ook/links/domains/sdm/schemas/{schema_name}/tables"
    )
    assert sdm["collections"]["columns"].endswith(
        "/ook/links/domains/sdm/schemas/{schema_name}"
        "/tables/{table_name}/columns"
    )

    python = domains["python"]
    assert python["entities"]["object"].endswith(
        "/ook/links/domains/python/objects/{name}"
    )
    assert python["collections"]["objects"].endswith(
        "/ook/links/domains/python/objects"
    )
    assert python["collections"]["children"].endswith(
        "/ook/links/domains/python/objects/{name}/children"
    )


@pytest.mark.asyncio
async def test_domains_index_agrees_with_domain_endpoints(
    client: AsyncClient,
) -> None:
    """An entry publishes exactly what the domain's own endpoint does.

    The index is a second place the templates are served from, so pin the
    two together: a domain that grows a template only in its own endpoint
    would otherwise leave the index quietly stale.
    """
    response = await client.get(DOMAINS_URL)
    assert response.status_code == 200

    entries = response.json()
    assert entries  # a domain-less index would pass the loop vacuously
    for entry in entries:
        domain_response = await client.get(entry["self_url"])

        assert domain_response.status_code == 200
        assert domain_response.json() == {
            "entities": entry["entities"],
            "collections": entry["collections"],
        }
