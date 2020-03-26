"""Tests for routing Kafka events into process_edition_updated."""

from __future__ import annotations

import asyncio
import datetime
from typing import TYPE_CHECKING

from ook.app import create_app
from tests.conftest import async_test_until

if TYPE_CHECKING:
    from _pytest.logging import LogCaptureFixture
    from aiohttp.pytest_plugin.test_utils import TestClient
    from aiokafka import AIOKafkaProducer
    from kafkit.registry.manager import RecordNameSchemaManager


async def test_process_edition_updated(
    aiohttp_client: TestClient,
    caplog: LogCaptureFixture,
    schema_manager: RecordNameSchemaManager,
    producer: AIOKafkaProducer,
) -> None:
    message_key = {"product_slug": "example", "edition_slug": "main"}

    message_value = {
        "event_type": "edition.updated",
        "event_timestamp": datetime.datetime.utcnow(),
        "product": {
            "published_url": "https://pipelines.lsst.io/",
            "url": "https://keeper.lsst.codes/products/pipelines",
            "slug": "pipelines",
        },
        "edition": {
            "published_url": "https://example.lsst.io/",
            "url": "https://keeper.lsst.codes/editions/68",
            "slug": "main",
            "build_url": "https://keeper.lsst.codes/builds/11318",
        },
    }

    message_key_bytes = await schema_manager.serialize(
        data=message_key, name="ltd.edition_key_v1"
    )
    message_value_bytes = await schema_manager.serialize(
        data=message_value, name="ltd.edition_update_v1"
    )

    # Send this message only to create the topic. Might be better to create
    # the topic directly
    await producer.send_and_wait(
        "ltd.events", key=message_key_bytes, value=message_value_bytes
    )
    await asyncio.sleep(2.0)

    app = create_app()
    await aiohttp_client(app)

    assert await async_test_until(
        lambda: "Got initial partition assignment for Kafka topics"
        in caplog.text,
        timeout=10.0,
    )

    await producer.send_and_wait(
        "ltd.events", key=message_key_bytes, value=message_value_bytes
    )

    assert await async_test_until(
        lambda: "In process_edition_updated" in caplog.text, timeout=10.0
    )

    assert await async_test_until(
        lambda: "Classified LTD site" in caplog.text, timeout=10.0
    )

    assert await async_test_until(
        lambda: "content_type=<ContentType.LTD_GENERIC: 'ltd_generic'>"
        in caplog.text,
        timeout=10.0,
    )
