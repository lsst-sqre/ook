"""Process edition.updated events."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any, Dict

from aiohttp import web

from ook.classification import ContentType, classify_ltd_site

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

__all__ = ["process_edition_updated"]


async def process_edition_updated(
    *,
    app: web.Application,
    logger: BoundLogger,
    message: Dict[str, Any],
) -> None:
    """Process an ``edition.updated`` event from LTD Events.

    Parameters
    ----------
    app : `aiohttp.web.Application`
        The app.
    logger
        A structlog logger that is bound with context about the Kafka message.
    message : `dict`
        The deserialized value of the Kafka message.
    """
    logger.info("In process_edition_updated")

    content_type = await classify_ltd_site(
        http_session=app["safir/http_session"],
        product_slug=message["product"]["slug"],
        published_url=message["edition"]["published_url"],
    )
    logger.info("Classified LTD site", content_type=content_type)

    ltd_document_types = {
        ContentType.LTD_LANDER_JSONLD,
        ContentType.LTD_SPHINX_TECHNOTE,
    }
    if content_type in ltd_document_types:
        await queue_ltd_document_ingest(
            app=app,
            logger=logger,
            content_type=content_type,
            edition_updated_message=message,
        )


async def queue_ltd_document_ingest(
    *,
    app: web.Application,
    logger: BoundLogger,
    content_type: ContentType,
    edition_updated_message: Dict[str, Any],
) -> None:
    """Add the LTD-based document to the ingest Kafka topic.

    Parameters
    ----------
    app : `aiohttp.web.Application`
        The app.
    logger
        A structlog logger that is bound with context about the Kafka message.
    content_type : `ook.classification.ContentType`
        The classified type of the document on LSST the Docs.
    message : `dict`
        The deserialized value of the ``edition.updated`` message from
        LTD Events, which should use the ``ltd.edition_update_v1`` schema.

    Notes
    -----
    This function produces a Kafka message to the topic configured as
    `~ook.config.Configuration.ingest_kafka_topic`. This is the central topic
    for processing any type of content into Algolia.

    The key uses the ``ook.url_key_v1`` schema, so that ingest requests for
    the same URL get processed in order; no accidental overwriting of an
    Algolia record because of out-of-order ingests.

    The value uses the ``ook.ltd_url_ingest_v1`` schema. This schema is
    specialized for ingest requests related to a URL hosted on LSST the Docs.
    The schema contains not only the URL, but also context about the edition
    and product that hosts that URL so that the Algolia ingest pipeline can
    relate records for this URL with other records related to that LTD product.
    """
    schema_manager = app["safir/schema_manager"]
    producer = app["safir/kafka_producer"]
    topic_name = app["safir/config"].ingest_kafka_topic

    key = {"url": edition_updated_message["edition"]["published_url"]}

    value = {
        "content_type": content_type.name,
        "request_timestamp": datetime.datetime.utcnow(),
        "update_timestamp": edition_updated_message["event_timestamp"],
        "url": edition_updated_message["edition"]["published_url"],
        "edition": edition_updated_message["edition"],
        "product": edition_updated_message["product"],
    }

    # Serialize message
    key_data = await schema_manager.serialize(data=key, name="ook.url_key_v1")
    value_data = await schema_manager.serialize(
        data=value, name="ook.ltd_url_ingest_v1"
    )

    # Produce message
    await producer.send_and_wait(topic_name, key=key_data, value=value_data)
    logger.info(
        "Produced an LTD document URL ingest request",
        topic=topic_name,
        url=edition_updated_message["edition"]["published_url"],
    )
