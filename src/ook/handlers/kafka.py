"""Consumer for Kafka topics."""

from typing import Annotated

from fastapi import Depends

from ook.config import config
from ook.dependencies.consumercontext import (
    ConsumerContext,
    consumer_context_dependency,
)
from ook.domain.algoliarecord import DocumentSourceType
from ook.domain.kafka import (
    CheckLinksMessageV1,
    LtdUrlIngestV2,
    RecheckUrlsMessageV1,
)
from ook.kafkarouter import kafka_router

__all__ = ["handle_linkcheck_execution", "handle_ltd_document_ingest"]


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

    factory = context.factory

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


@kafka_router.subscriber(
    config.linkcheck_kafka_topic, group_id=config.kafka_consumer_group_id
)
async def handle_linkcheck_execution(
    message: CheckLinksMessageV1 | RecheckUrlsMessageV1,
    context: Annotated[ConsumerContext, Depends(consumer_context_dependency)],
) -> None:
    """Handle a message requesting execution of a submitted link check
    or a scheduled recheck of stored URLs.
    """
    factory = context.factory
    service = factory.create_linkcheck_service()

    if isinstance(message, CheckLinksMessageV1):
        context.rebind_logger(check_id=message.check_id)
        logger = context.logger
        logger.info("Starting link-check execution request.")
        async with factory.db_session.begin():
            await service.execute_check(message.check_id)
        logger.info("Finished link-check execution request.")
    else:
        context.rebind_logger(url_count=len(message.url_ids))
        logger = context.logger
        logger.info("Starting link-recheck request.")
        async with factory.db_session.begin():
            await service.execute_recheck(message.url_ids)
        logger.info("Finished link-recheck request.")
