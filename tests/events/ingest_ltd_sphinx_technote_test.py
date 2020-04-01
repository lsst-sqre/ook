"""End-to-end test of routing Kafka events from ook.ingest through the
ingest_ltd_sphinx_technote handler.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from ook.app import create_app
from tests.conftest import async_test_until

if TYPE_CHECKING:
    from _pytest.logging import LogCaptureFixture
    from aiohttp.pytest_plugin.test_utils import TestClient
    from aiokafka import AIOKafkaProducer


async def test_process_edition_updated(
    aiohttp_client: TestClient,
    caplog: LogCaptureFixture,
    producer: AIOKafkaProducer,
) -> None:
    ingest_message_key_schema = "ook.url_key_v1"
    ingest_message_key = {"url": "https://sqr-035.lsst.io/"}

    ingest_message_value_schema = "ook.ltd_url_ingest_v1"
    ingest_message_value = {
        "content_type": "LTD_SPHINX_TECHNOTE",
        "request_timestamp": datetime.datetime.utcnow(),
        "update_timestamp": datetime.datetime.utcnow(),
        "url": "https://sqr-035.lsst.io/",
        "edition": {
            "url": "https://keeper.lsst.codes/editions/3562",
            "published_url": "https://sqr-035.lsst.io/",
            "slug": "main",
            "build_url": "https://keeper.lsst.codes/builds/11446",
        },
        "product": {
            "url": "https://keeper.lsst.codes/products/sqr-035",
            "published_url": "https://sqr-035.lsst.io/",
            "slug": "sqr-035",
        },
    }

    app = create_app(enable_ltd_events_kafka_topic=False)
    await aiohttp_client(app)

    assert await async_test_until(
        lambda: "Got initial partition assignment for Kafka topics"
        in caplog.text,
        timeout=10.0,
    )

    schema_manager = app["safir/schema_manager"]

    message_key_bytes = await schema_manager.serialize(
        data=ingest_message_key, name=ingest_message_key_schema
    )
    message_value_bytes = await schema_manager.serialize(
        data=ingest_message_value, name=ingest_message_value_schema
    )
    await app["safir/kafka_producer"].send_and_wait(
        app["safir/config"].ingest_kafka_topic,
        key=message_key_bytes,
        value=message_value_bytes,
    )

    assert await async_test_until(
        lambda: "Starting LTD_SPHINX_TECHNOTE ingest" in caplog.text,
        timeout=10.0,
    )

    assert await async_test_until(
        lambda: "Finished building records" in caplog.text, timeout=10.0,
    )
