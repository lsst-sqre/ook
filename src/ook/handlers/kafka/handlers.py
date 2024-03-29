"""Handler functions for Kafka messages.

These functions are registered with the PydanticAIOKafkaConsumer in the
router module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dataclasses_avroschema.avrodantic import AvroBaseModel
from structlog import get_logger
from structlog.stdlib import BoundLogger

from ook.domain.algoliarecord import DocumentSourceType
from ook.domain.kafka import LtdUrlIngestV2, UrlIngestKeyV1
from ook.factory import Factory

if TYPE_CHECKING:
    from .router import MessageMetadata

__all__ = ["handle_ltd_document_ingest"]


def bind_logger_with_message_metadata(
    logger: BoundLogger,
    *,
    message_metadata: MessageMetadata,
    key: AvroBaseModel,
    value: AvroBaseModel,
) -> BoundLogger:
    """Bind a logger with message metadata."""
    return logger.bind(
        kafka_topic=message_metadata.topic,
        kafka_partition=message_metadata.partition,
        kafka_offset=message_metadata.offset,
        kafka_key=key.dict(),
        kafka_value=value.dict(),
        serialized_key_size=message_metadata.serialized_key_size,
        serialized_value_size=message_metadata.serialized_value_size,
        kafka_headers=message_metadata.headers,
    )


async def handle_ltd_document_ingest(
    *,
    message_metadata: MessageMetadata,
    key: UrlIngestKeyV1,
    value: LtdUrlIngestV2,
    **kwargs: Any,
) -> None:
    """Handle a message requesting an ingest for an LTD document."""
    logger = bind_logger_with_message_metadata(
        get_logger("ook"),
        message_metadata=message_metadata,
        key=key,
        value=value,
    )
    logger = logger.bind(
        ltd_slug=value.project.slug, content_type=value.content_type.value
    )

    logger.info(
        "Starting processing of LTD document ingest request.",
    )

    factory = await Factory.create(logger=logger)

    if value.content_type == DocumentSourceType.LTD_TECHNOTE:
        technote_service = factory.create_technote_ingest_service()
        await technote_service.ingest(
            published_url=value.url,
            project_url=value.project.url,
            edition_url=value.edition.url,
        )
    elif value.content_type == DocumentSourceType.LTD_SPHINX_TECHNOTE:
        sphinx_technote_service = (
            factory.create_sphinx_technote_ingest_service()
        )
        await sphinx_technote_service.ingest(
            published_url=value.url,
            project_url=value.project.url,
            edition_url=value.edition.url,
        )
    elif value.content_type == DocumentSourceType.LTD_LANDER_JSONLD:
        lander_service = factory.create_lander_ingest_service()
        await lander_service.ingest(
            published_url=value.url,
        )

    logger.info("Finished processing LTD document ingest request.")
