"""Consumer for Kafka topics."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from ook.config import config
from ook.dependencies.consumercontext import (
    ConsumerContext,
    consumer_context_dependency,
)
from ook.domain.algoliarecord import DocumentSourceType
from ook.domain.kafka import LtdUrlIngestV2
from ook.factory import Factory
from ook.kafkarouter import kafka_router

__all__ = ["handle_ltd_document_ingest"]


@kafka_router.subscriber(
    config.ingest_kafka_topic, group_id=config.kafka_consumer_group_id
)
async def handle_ltd_document_ingest(
    message: LtdUrlIngestV2,
    context: Annotated[ConsumerContext, Depends(consumer_context_dependency)],
) -> None:
    """Handle a message requesting an ingest for an LTD document."""
    logger = context.logger
    logger = logger.bind(
        ltd_slug=message.project.slug, content_type=message.content_type.value
    )

    logger.info(
        "Starting processing of LTD document ingest request.",
    )

    factory = await Factory.create(logger=logger)

    content_type = message.content_type

    if content_type == DocumentSourceType.LTD_TECHNOTE:
        technote_service = factory.create_technote_ingest_service()
        await technote_service.ingest(
            published_url=message.url,
            project_url=message.project.url,
            edition_url=message.edition.url,
        )
    elif content_type == DocumentSourceType.LTD_SPHINX_TECHNOTE:
        sphinx_technote_service = (
            factory.create_sphinx_technote_ingest_service()
        )
        await sphinx_technote_service.ingest(
            published_url=message.url,
            project_url=message.project.url,
            edition_url=message.edition.url,
        )
    elif content_type == DocumentSourceType.LTD_LANDER_JSONLD:
        lander_service = factory.create_lander_ingest_service()
        await lander_service.ingest(
            published_url=message.url,
        )

    logger.info("Finished processing LTD document ingest request.")
