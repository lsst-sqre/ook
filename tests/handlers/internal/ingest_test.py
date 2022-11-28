"""Tests for /ingest routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ook.app import create_app
from tests.conftest import async_test_until

if TYPE_CHECKING:
    from _pytest.logging import LogCaptureFixture
    from aiohttp.pytest_plugin.test_utils import TestClient


@pytest.mark.asyncio
async def test_post_ingest_ltd_single(
    aiohttp_client: TestClient, caplog: LogCaptureFixture
) -> None:
    """Test POST /ingest/ltd with a single product_slug."""
    app = create_app(
        enable_ingest_kafka_topic=False, enable_ltd_events_kafka_topic=True
    )
    client = await aiohttp_client(app)

    response = await client.post(
        "/ingest/ltd", json={"edition_slug": "main", "product_slug": "sqr-006"}
    )
    assert response.status == 202

    assert await async_test_until(
        lambda: "Produced an LTD document URL ingest request" in caplog.text,
        timeout=10.0,
    )


@pytest.mark.asyncio
async def test_post_ingest_ltd_list(
    aiohttp_client: TestClient, caplog: LogCaptureFixture
) -> None:
    """Test POST /ingest/ltd with a list of product_slugs."""
    app = create_app(
        enable_ingest_kafka_topic=False, enable_ltd_events_kafka_topic=True
    )
    client = await aiohttp_client(app)

    response = await client.post(
        "/ingest/ltd",
        json={"edition_slug": "main", "product_slugs": ["sqr-006", "sqr-035"]},
    )
    assert response.status == 202

    assert await async_test_until(
        lambda: "url=https://sqr-006.lsst.io" in caplog.text,
        timeout=10.0,
    )

    assert await async_test_until(
        lambda: "url=https://sqr-035.lsst.io" in caplog.text,
        timeout=10.0,
    )


@pytest.mark.asyncio
async def test_post_ingest_ltd_pattern(
    aiohttp_client: TestClient, caplog: LogCaptureFixture
) -> None:
    """Test POST /ingest/ltd with a regular expression pattern of product
    slugs.
    """
    app = create_app(
        enable_ingest_kafka_topic=False, enable_ltd_events_kafka_topic=True
    )
    client = await aiohttp_client(app)

    response = await client.post(
        "/ingest/ltd",
        json={
            "edition_slug": "main",
            "product_slug_pattern": r"sqr-(000|032)",
        },
    )
    assert response.status == 202

    assert await async_test_until(
        lambda: "url=https://sqr-000.lsst.io" in caplog.text,
        timeout=10.0,
    )

    assert await async_test_until(
        lambda: "url=https://sqr-032.lsst.io" in caplog.text,
        timeout=10.0,
    )
