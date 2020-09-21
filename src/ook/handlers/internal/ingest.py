"""Hander for ``/ingest``."""

from __future__ import annotations

import datetime
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from aiohttp import ClientSession, web
from pydantic import BaseModel, ValidationError, root_validator

from ook.classification import ContentType, classify_ltd_site
from ook.handlers import internal_routes

if TYPE_CHECKING:
    from aiokafka import AIOKafkaProducer
    from kafkit.registry.manager import RecordNameSchemaManager
    from structlog._config import BoundLoggerLazyProxy

    from ook.config import Configuration

__all__ = ["post_ingest_ltd"]


@internal_routes.post("/ingest/ltd")
async def post_ingest_ltd(request: web.Request) -> web.Response:
    """``POST /ingest/ltd`` for triggering bulk ingest of LSST the Docs
    products.
    """
    request_data = await request.json()
    try:
        parsed_request = LtdIngestRequest(**request_data)
    except ValidationError as e:
        return web.json_response(e.errors(), status=400)

    if parsed_request.product_slug_pattern is not None:
        await request.config_dict["ook/scheduler"].spawn(
            _queue_pattern_ltd_product_ingest(
                session=request.config_dict["safir/http_session"],
                logger=request["safir/logger"],
                config=request.config_dict["safir/config"],
                producer=request.config_dict["safir/kafka_producer"],
                schema_manager=request.config_dict["safir/schema_manager"],
                product_pattern=parsed_request.product_slug_pattern,
                edition_slug=parsed_request.edition_slug,
            )
        )

    if parsed_request.product_slugs is not None:
        await request.config_dict["ook/scheduler"].spawn(
            _queue_list_ltd_product_ingest(
                session=request.config_dict["safir/http_session"],
                logger=request["safir/logger"],
                config=request.config_dict["safir/config"],
                producer=request.config_dict["safir/kafka_producer"],
                schema_manager=request.config_dict["safir/schema_manager"],
                product_slugs=parsed_request.product_slugs,
                edition_slug=parsed_request.edition_slug,
            )
        )

    if parsed_request.product_slug is not None:
        await request.config_dict["ook/scheduler"].spawn(
            _queue_single_ltd_product_ingest(
                session=request.config_dict["safir/http_session"],
                logger=request["safir/logger"],
                config=request.config_dict["safir/config"],
                producer=request.config_dict["safir/kafka_producer"],
                schema_manager=request.config_dict["safir/schema_manager"],
                product_slug=parsed_request.product_slug,
                edition_slug=parsed_request.edition_slug,
            )
        )

    return web.json_response({}, status=202)


class LtdIngestRequest(BaseModel):
    """Schema for `post_ingest_ltd`."""

    product_slug: Optional[str]

    product_slugs: Optional[List[str]]

    product_slug_pattern: Optional[str]

    edition_slug: str = "main"

    @root_validator
    def check_slug(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        product_slug = values.get("product_slug")
        product_slugs = values.get("product_slugs")
        product_slug_pattern = values.get("product_slug_pattern")

        if (
            product_slug is None
            and product_slugs is None
            and product_slug_pattern is None
        ):
            raise ValueError(
                "One of the ``product_slug``, ``product_slugs`` or "
                "``product_slug_pattern`` fields is required."
            )

        if product_slug_pattern is not None:
            try:
                re.compile(product_slug_pattern)
            except Exception:
                raise ValueError(
                    "product_slug_pattern {self.product_slug_pattern!r} is "
                    "not a valid Python regular expression."
                )

        return values


async def _queue_pattern_ltd_product_ingest(
    *,
    session: ClientSession,
    logger: BoundLoggerLazyProxy,
    config: Configuration,
    producer: AIOKafkaProducer,
    schema_manager: RecordNameSchemaManager,
    product_pattern: str,
    edition_slug: str,
) -> None:
    """Queue a LTD-based documents with product slugs matching a regular
    expression pattern for ingest in the ook.ingest Kafka topic.
    """
    product_data = await _get_json(
        session=session, url="https://keeper.lsst.codes/products/"
    )
    url_prefix = "https://keeper.lsst.codes/products/"
    all_products = [p[len(url_prefix) :] for p in product_data["products"]]
    pattern = re.compile(product_pattern)
    matching_products = [
        p for p in all_products if pattern.match(p) is not None
    ]
    logger.info("Matched products", product_slugs=matching_products)
    await _queue_list_ltd_product_ingest(
        session=session,
        logger=logger,
        config=config,
        producer=producer,
        schema_manager=schema_manager,
        product_slugs=matching_products,
        edition_slug=edition_slug,
    )


async def _queue_list_ltd_product_ingest(
    *,
    session: ClientSession,
    logger: BoundLoggerLazyProxy,
    config: Configuration,
    producer: AIOKafkaProducer,
    schema_manager: RecordNameSchemaManager,
    product_slugs: List[str],
    edition_slug: str,
) -> None:
    """Queue a list of LTD-based documents (with known product slugs) for
    ingest in the ook.ingest Kafka topic.
    """
    for product_slug in product_slugs:
        try:
            await _queue_single_ltd_product_ingest(
                session=session,
                logger=logger,
                config=config,
                producer=producer,
                schema_manager=schema_manager,
                product_slug=product_slug,
                edition_slug=edition_slug,
            )
        except Exception:
            logger.exception(
                "Failed to queue LTD product ingest",
                product_slug=product_slug,
                edition_slug=edition_slug,
            )


async def _queue_single_ltd_product_ingest(
    *,
    session: ClientSession,
    logger: BoundLoggerLazyProxy,
    config: Configuration,
    producer: AIOKafkaProducer,
    schema_manager: RecordNameSchemaManager,
    product_slug: str,
    edition_slug: str,
) -> None:
    """Queue an LTD-based document for ingest in the ook.ingest Kafka topic."""
    product_data = await _get_json(
        session=session,
        url=f"https://keeper.lsst.codes/products/{product_slug}",
    )
    edition_urls = await _get_json(
        session=session,
        url=f"https://keeper.lsst.codes/products/{product_slug}/editions/",
    )
    for edition_url in edition_urls["editions"]:
        edition_data = await _get_json(session=session, url=edition_url)
        if edition_data["slug"] == edition_slug:
            break
    if edition_data["slug"] != edition_slug:
        raise RuntimeError(
            "Could not find slug {edition_slug} for product {product_slug}"
        )

    content_type = await classify_ltd_site(
        http_session=session,
        product_slug=product_slug,
        published_url=edition_data["published_url"],
    )
    ltd_document_types = {
        ContentType.LTD_LANDER_JSONLD,
        ContentType.LTD_SPHINX_TECHNOTE,
    }
    if content_type not in ltd_document_types:
        logger.warning(
            "Cannot do triggered ingest of a non-document " "LTD product.",
            content_type=content_type.name,
        )
        return

    key = {"url": edition_data["published_url"]}
    value = {
        "content_type": content_type.name,
        "request_timestamp": datetime.datetime.utcnow(),
        "update_timestamp": datetime.datetime.utcnow(),
        "url": edition_data["published_url"],
        "edition": {
            "url": edition_data["self_url"],
            "published_url": edition_data["published_url"],
            "slug": edition_slug,
            "build_url": edition_data["build_url"],
        },
        "product": {
            "url": product_data["self_url"],
            "published_url": edition_data["published_url"],
            "slug": product_slug,
        },
    }
    key_data = await schema_manager.serialize(data=key, name="ook.url_key_v1")
    value_data = await schema_manager.serialize(
        data=value, name="ook.ltd_url_ingest_v1"
    )
    # Produce message
    topic_name = config.ingest_kafka_topic
    await producer.send_and_wait(topic_name, key=key_data, value=value_data)
    logger.info(
        "Produced an LTD document URL ingest request",
        topic=topic_name,
        url=value["url"],
    )


async def _get_json(
    *,
    session: ClientSession,
    url: str,
) -> Dict[str, Any]:
    response = await session.get(url)
    if response.status != 200:
        raise RuntimeError(f"Error requesting {url}")
    return await response.json()
