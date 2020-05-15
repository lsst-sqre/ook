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
            "published_url": "https://sqr-000.lsst.io",
            "url": "https://keeper.lsst.codes/products/sqr-000",
            "slug": "sqr-000",
        },
        "edition": {
            "published_url": "https://sqr-000.lsst.io",
            "url": "https://keeper.lsst.codes/editions/21",
            "slug": "main",
            "build_url": "https://keeper.lsst.codes/builds/2775",
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

    app = create_app(enable_ingest_kafka_topic=False)
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
        lambda: "content_type=<ContentType.LTD_SPHINX_TECHNOTE: "
        "'ltd_sphinx_technote'>" in caplog.text,
        timeout=10.0,
    )

    assert await async_test_until(
        lambda: "Produced an LTD document URL ingest request" in caplog.text,
        timeout=10.0,
    )
