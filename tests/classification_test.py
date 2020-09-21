"""Tests for the ook.classification module."""

from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from ook.classification import (
    ContentType,
    classify_ltd_site,
    has_jsonld_metadata,
    is_document_handle,
)


@pytest.mark.parametrize(
    "product_slug,published_url,expected",
    [
        ("ldm-151", "https://ldm-151.lsst.io", ContentType.LTD_LANDER_JSONLD),
        (
            "sqr-000",
            "https://sqr-000.lsst.io",
            ContentType.LTD_SPHINX_TECHNOTE,
        ),
        # Generic because it doesn't have a JSON-LD metadata file (currently)
        ("dmtn-097", "https://dmtn-097.lsst.io", ContentType.LTD_GENERIC),
        ("pipelines", "https://pipelines.lsst.io", ContentType.LTD_GENERIC),
    ],
)
async def test_classify_ltd_site(
    product_slug: str,
    published_url: str,
    expected: ContentType,
) -> None:
    """Test classify_ltd_site (uses real HTTP requests)."""
    async with aiohttp.ClientSession() as http_session:
        assert (
            await classify_ltd_site(
                http_session=http_session,
                product_slug=product_slug,
                published_url=published_url,
            )
            is expected
        )


@pytest.mark.parametrize(
    "product_slug,expected", [("pipelines", False), ("sqr-000", True)]
)
def test_is_document_handle(product_slug: str, expected: bool) -> None:
    """Test ``is_document_handle`` function."""
    assert is_document_handle(product_slug) == expected


@pytest.mark.parametrize(
    "base_url,mock_url,status,expected",
    [
        (
            "https://ldm-151.lsst.io",
            "https://ldm-151.lsst.io/metadata.jsonld",
            200,
            True,
        ),
        (
            "https://ldm-151.lsst.io/",
            "https://ldm-151.lsst.io/metadata.jsonld",
            200,
            True,
        ),
        (
            "https://sqr-000.lsst.io/",
            "https://sqr-000.lsst.io/metadata.jsonld",
            404,
            False,
        ),
    ],
)
async def test_has_jsonld_metadata(
    base_url: str, mock_url: str, status: int, expected: bool
) -> None:
    """Test the has_jsonld_metadata function (using mocked HTTP)."""
    with aioresponses() as m:
        m.head(mock_url, status=status, body="")

        session = aiohttp.ClientSession()

        assert (
            await has_jsonld_metadata(
                http_session=session, published_url=base_url
            )
            is expected
        )
