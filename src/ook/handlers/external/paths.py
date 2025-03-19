"""Handlers for the app's external root endpoints, ``/ook/``."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import AnyHttpUrl
from safir.metadata import get_metadata
from safir.models import ErrorModel

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency
from ook.exceptions import NotFoundError

from .models import IndexResponse, Link, LinksResponse, LtdIngestRequest

__all__ = ["external_router", "get_index"]

external_router = APIRouter()
"""FastAPI router for all external handlers."""


@external_router.get(
    "/",
    response_model_exclude_none=True,
    summary="Application metadata",
)
async def get_index(
    request: Request,
) -> IndexResponse:
    """GET metadata about the application."""
    metadata = get_metadata(
        package_name="ook",
        application_name=config.name,
    )
    # Construct these URLs; this doesn't use request.url_for because the
    # endpoints are in other FastAPI "apps".
    doc_url = request.url.replace(path=f"/{config.path_prefix}/redoc")
    return IndexResponse(
        metadata=metadata,
        api_docs=AnyHttpUrl(str(doc_url)),
    )


@external_router.post(
    "/ingest/ltd",
    summary="Ingest a project in LSST the Docs",
    response_model=None,
)
async def post_ingest_ltd(
    ingest_request: LtdIngestRequest,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> Response:
    """Trigger an ingest of a project in LSST the Docs."""
    logger = context.logger
    logger.info(
        "Received request to ingest a project in LSST the Docs.",
        payload=ingest_request.model_dump(),
    )
    classifier = context.factory.create_classification_service()
    async with asyncio.TaskGroup() as task_group:
        if ingest_request.product_slug is not None:
            task_group.create_task(
                classifier.queue_ingest_for_ltd_product_slug(
                    product_slug=ingest_request.product_slug,
                    edition_slug=ingest_request.edition_slug,
                )
            )
        if ingest_request.product_slugs is not None:
            for product_slug in ingest_request.product_slugs:
                task_group.create_task(
                    classifier.queue_ingest_for_ltd_product_slug(
                        product_slug=product_slug,
                        edition_slug=ingest_request.edition_slug,
                    )
                )
        if ingest_request.product_slug_pattern is not None:
            task_group.create_task(
                classifier.queue_ingest_for_ltd_product_slug_pattern(
                    product_slug_pattern=ingest_request.product_slug_pattern,
                    edition_slug=ingest_request.edition_slug,
                )
            )
    return Response(status_code=202)


@external_router.post(
    "/ingest/sdm-schemas",
    summary="Ingest SDM schemas (doc links)",
)
async def post_ingest_sdm_schemas(
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> Response:
    """Trigger an ingest of SDM schemas."""
    logger = context.logger
    logger.info("Received request to ingest SDM schemas.")
    async with context.session.begin():
        ingest_service = (
            await context.factory.create_sdm_schemas_ingest_service()
        )
        await ingest_service.ingest()
        await context.session.commit()
    return Response(status_code=200)


@external_router.get(
    "/links/domains/sdm-schemas/schemas/{schema_name}",
    summary="Get documentation links for a SDM schema",
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def get_sdm_schema_links(
    schema_name: str,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> LinksResponse:
    """Get documentation links for a SDM schema."""
    logger = context.logger
    logger.info(
        "Received request to get documentation links for a SDM schema."
    )
    async with context.session.begin():
        link_service = context.factory.create_links_service()
        link = await link_service.get_links_for_sdm_schema(schema_name)
        if link is None:
            raise NotFoundError(
                f"No links found for SDM schema {schema_name}."
            )
        return LinksResponse(links=[Link.from_domain(link)])
