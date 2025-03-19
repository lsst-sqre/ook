"""Endpoints for /ook/ingest/ APIs."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency

from .models import LtdIngestRequest

router = APIRouter(prefix=f"{config.path_prefix}/ingest", tags=["ingest"])
"""FastAPI router for all ingest handlers."""


@router.post(
    "/ltd",
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


@router.post(
    "/sdm-schemas",
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
