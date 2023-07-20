"""Handlers for the app's external root endpoints, ``/ook/``."""

import asyncio

from fastapi import APIRouter, Depends, Request, Response
from pydantic import AnyHttpUrl
from safir.metadata import get_metadata

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency

from .models import IndexResponse, LtdIngestRequest

__all__ = ["external_router", "get_index"]

external_router = APIRouter()
"""FastAPI router for all external handlers."""


@external_router.get(
    "/",
    response_model=IndexResponse,
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
        api_docs=AnyHttpUrl(str(doc_url), scheme=request.url.scheme),
    )


@external_router.post(
    "/ingest/ltd",
    summary="Ingest a project in LSST the Docs",
    response_model=None,
)
async def post_ingest_ltd(
    ingest_request: LtdIngestRequest,
    context: RequestContext = Depends(context_dependency),
) -> Response:
    """Trigger an ingest of a project in LSST the Docs."""
    logger = context.logger
    logger.info(
        "Received request to ingest a project in LSST the Docs.",
        payload=ingest_request.dict(),
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
    return Response(status_code=202)
