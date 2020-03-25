"""Routes Kafka messages to processing functions."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import structlog
from aiohttp import web
from aiokafka import AIOKafkaConsumer
from kafkit.registry import Deserializer
from kafkit.registry.aiohttp import RegistryApi

__all__ = ["consume_events"]


async def consume_events(app: web.Application) -> None:
    """The main Kafka consumer, which routes messages to processing functions
    or tasks.
    """
    logger = structlog.get_logger(app["safir/config"].logger_name)

    registry = RegistryApi(
        session=app["safir/http_session"],
        url=app["safir/config"].schema_registry_url,
    )
    deserializer = Deserializer(registry=registry)

    consumer_settings = {
        "bootstrap_servers": app["safir/config"].kafka_broker_url,
        "group_id": app["safir/config"].kafka_consumer_group_id,
        "auto_offset_reset": "latest",
        "security_protocol": app["safir/kafka_protocol"],
    }
    if consumer_settings["security_protocol"] == "SSL":
        consumer_settings["ssl_context"] = app["safir/kafka_ssl_context"]
    consumer = AIOKafkaConsumer(
        loop=asyncio.get_event_loop(), **consumer_settings
    )

    topic_names = get_configured_topics(app)

    try:
        await consumer.start()
        logger.info("Started Kafka consumer")

        logger.info("Subscribing to Kafka topics", names=topic_names)
        consumer.subscribe(topic_names)

        partitions = consumer.assignment()
        while len(partitions) == 0:
            # Wait for the consuemr to get partition assignment
            await asyncio.sleep(1.0)
            partitions = consumer.assignment()
        logger.info(
            "Got initial partition assignment for Kafka topics",
            partitions=[str(p) for p in partitions],
        )

        async for message in consumer:
            try:
                value_info = await deserializer.deserialize(
                    message.value, include_schema=True
                )
            except Exception:
                logger.exception(
                    "Failed to deserialize a Kafka message value",
                    topic=message.topic,
                    partition=message.partition,
                    offset=message.offset,
                )
                continue

            try:
                await route_message(
                    app=app,
                    message=value_info["message"],
                    schema_id=value_info["id"],
                    schema=value_info["schema"],
                    topic=message.topic,
                    partition=message.partition,
                    offset=message.offset,
                )
            except Exception:
                logger.exception(
                    "Failed to route a Kafka message",
                    topic=message.topic,
                    partition=message.partition,
                    offset=message.offset,
                )

    except asyncio.CancelledError:
        logger.info("consume_events task got cancelled")
    finally:
        logger.info("consume_events task cancelling")
        await consumer.stop()


def get_configured_topics(app: web.Application) -> List[str]:
    """Get the list of topic names that this app is configured to consume."""
    return [app["safir/config"].ltd_events_kafka_topic]


async def route_message(
    *,
    app: web.Application,
    message: Dict[str, Any],
    schema_id: int,
    schema: Dict[str, Any],
    topic: str,
    partition: Any,
    offset: int,
) -> None:
    """Route a Kafka message to a processing function."""
    pass
