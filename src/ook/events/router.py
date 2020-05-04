"""Routes Kafka messages to processing functions."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List

import aiojobs
import structlog
from aiohttp import web
from aiokafka import AIOKafkaConsumer
from kafkit.registry import Deserializer
from kafkit.registry.aiohttp import RegistryApi

from ook.classification import ContentType
from ook.events.editionupdated import process_edition_updated
from ook.ingest.workflows.ltdlander import ingest_ltd_lander_jsonld_document
from ook.ingest.workflows.ltdsphinxtechnote import ingest_ltd_sphinx_technote

if TYPE_CHECKING:
    from structlog._config import BoundLoggerLazyProxy

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
        "security_protocol": app["safir/config"].kafka_protocol,
    }
    if consumer_settings["security_protocol"] == "SSL":
        consumer_settings["ssl_context"] = app["safir/kafka_ssl_context"]
    consumer = AIOKafkaConsumer(
        loop=asyncio.get_event_loop(), **consumer_settings
    )

    topic_names = get_configured_topics(app)

    scheduler = await aiojobs.create_scheduler()

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
                    scheduler=scheduler,
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
        await scheduler.close()


def get_configured_topics(app: web.Application) -> List[str]:
    """Get the list of topic names that this app is configured to consume."""
    topic_names: List[str] = []

    config = app["safir/config"]

    if config.enable_ltd_events_kafka_topic:
        topic_names.append(config.ltd_events_kafka_topic)

    if config.enable_ingest_kafka_topic:
        topic_names.append(config.ingest_kafka_topic)

    return topic_names


async def route_message(
    *,
    app: web.Application,
    scheduler: aiojobs.Scheduler,
    message: Dict[str, Any],
    schema_id: int,
    schema: Dict[str, Any],
    topic: str,
    partition: int,
    offset: int,
) -> None:
    """Route a Kafka message to a processing function.

    Parameters
    ----------
    app : `aiohttp.web.Application`
        The app.
    scheduler : `aiojobs.Scheduler`
        The aiojobs scheduler for jobs that handle the Kafka events.
    logger
        A structlog logger that is bound with context about the Kafka message.
    message : `dict`
        The deserialized value of the Kafka message.
    schema_id : `int`
        The Schema Registry ID of the Avro schema used to serialie the message.
    topic : `str`
        The name of the Kafka topic that the message was consumed from.
    partition : `int`
        The partition of the Kafka topic that the message was consumed form.
    offset : `int`
        The offset of the Kafka message.
    """
    logger = structlog.get_logger(app["safir/config"].logger_name)
    logger = logger.bind(
        topic=topic, partition=partition, offset=offset, schema_id=schema_id
    )

    if topic == app["safir/config"].ltd_events_kafka_topic:
        await route_ltd_events_message(
            app=app,
            scheduler=scheduler,
            logger=logger,
            message=message,
            schema_id=schema_id,
            schema=schema,
            topic=topic,
            partition=partition,
            offset=offset,
        )
    elif topic == app["safir/config"].ingest_kafka_topic:
        await route_ingest_message(
            app=app,
            scheduler=scheduler,
            logger=logger,
            message=message,
            schema_id=schema_id,
            schema=schema,
            topic=topic,
            partition=partition,
            offset=offset,
        )
    else:
        logger.warning(
            "Got a Kafka message but there is not handler for that topic"
        )


async def route_ltd_events_message(
    *,
    app: web.Application,
    scheduler: aiojobs.Scheduler,
    logger: BoundLoggerLazyProxy,
    message: Dict[str, Any],
    schema_id: int,
    schema: Dict[str, Any],
    topic: str,
    partition: int,
    offset: int,
) -> None:
    """Route a message known to be from the ltd.events topic.

    Parameters
    ----------
    app : `aiohttp.web.Application`
        The app.
    scheduler : `aiojobs.Scheduler`
        The aiojobs scheduler for jobs that handle the Kafka events.
    logger
        A structlog logger that is bound with context about the Kafka message.
    message : `dict`
        The deserialized value of the Kafka message.
    schema_id : `int`
        The Schema Registry ID of the Avro schema used to serialie the message.
    topic : `str`
        The name of the Kafka topic that the message was consumed from.
    partition : `int`
        The partition of the Kafka topic that the message was consumed form.
    offset : `int`
        The offset of the Kafka message.
    """
    event_type = message["event_type"]
    if event_type == "edition.updated":
        if message["edition"]["slug"] == "main":
            # We index only the "main" editions of documentation products
            await scheduler.spawn(
                process_edition_updated(
                    app=app, logger=logger, message=message
                )
            )


async def route_ingest_message(
    *,
    app: web.Application,
    scheduler: aiojobs.Scheduler,
    logger: BoundLoggerLazyProxy,
    message: Dict[str, Any],
    schema_id: int,
    schema: Dict[str, Any],
    topic: str,
    partition: int,
    offset: int,
) -> None:
    """Route a message known to be from the ook.ingest topic.

    Parameters
    ----------
    app : `aiohttp.web.Application`
        The app.
    scheduler : `aiojobs.Scheduler`
        The aiojobs scheduler for jobs that handle the Kafka events.
    logger
        A structlog logger that is bound with context about the Kafka message.
    message : `dict`
        The deserialized value of the Kafka message.
    schema_id : `int`
        The Schema Registry ID of the Avro schema used to serialie the message.
    topic : `str`
        The name of the Kafka topic that the message was consumed from.
    partition : `int`
        The partition of the Kafka topic that the message was consumed form.
    offset : `int`
        The offset of the Kafka message.
    """
    content_type = ContentType[message["content_type"]]
    if content_type == ContentType.LTD_SPHINX_TECHNOTE:
        await scheduler.spawn(
            ingest_ltd_sphinx_technote(
                app=app, logger=logger, url_ingest_message=message
            )
        )
    elif content_type == ContentType.LTD_LANDER_JSONLD:
        await scheduler.spawn(
            ingest_ltd_lander_jsonld_document(
                app=app, logger=logger, url_ingest_message=message
            )
        )
    else:
        logger.info(
            "Ignoring ingest request for unsupported content type",
            content_url=message["url"],
            content_type=message["content_type"],
        )
