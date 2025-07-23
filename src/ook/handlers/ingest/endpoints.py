"""Endpoints for /ook/ingest/ APIs."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency
from ook.domain.resources import Document

from ..resources.models import DocumentResource
from .models import (
    DocumentIngestRequest,
    LsstTexmfIngestRequest,
    LtdIngestRequest,
    SdmSchemasIngestRequest,
)

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
    ingest_request: SdmSchemasIngestRequest,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> Response:
    """Trigger an ingest of SDM schemas."""
    logger = context.logger
    logger.info("Received request to ingest SDM schemas.")
    async with context.session.begin():
        ingest_service = (
            await context.factory.create_sdm_schemas_ingest_service(
                github_owner=ingest_request.github_owner,
                github_repo=ingest_request.github_repo,
            )
        )
        await ingest_service.ingest(ingest_request.github_release_tag)
        await context.session.commit()
    return Response(status_code=200)


@router.post(
    "/lsst-texmf",
    summary="Ingest lsst-texmf (author info and glossary)",
)
async def post_ingest_lsst_texmf(
    ingest_request: LsstTexmfIngestRequest,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> Response:
    """Trigger an ingest of lsst-texmf."""
    logger = context.logger
    logger.info("Received request to ingest lsst-texmf.")
    async with context.session.begin():
        ingest_service = (
            await context.factory.create_lsst_texmf_ingest_service()
        )
        await ingest_service.ingest(
            git_ref=ingest_request.git_ref,
            ingest_authordb=ingest_request.ingest_authordb,
            ingest_glossary=ingest_request.ingest_glossary,
            delete_stale_records=ingest_request.delete_stale_records,
        )
        await context.session.commit()
    return Response(status_code=200)


@router.post(
    "/resources/documents",
    summary="Ingest document resources",
)
async def post_ingest_documents(
    ingest_request: DocumentIngestRequest,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> list[DocumentResource]:
    """Ingest document resources into the bibliography database.

    This endpoint accepts a list of documents to ingest. Each document
    will be assigned a new ID and timestamps, then upserted into the database.
    The endpoint returns the documents as stored in the database.
    """
    logger = context.logger
    logger.info(
        "Received request to ingest documents.",
        document_count=len(ingest_request.documents),
    )

    # Convert request models to domain models
    documents = [
        doc_request.to_domain() for doc_request in ingest_request.documents
    ]

    # Get the resource service and upsert documents
    resource_service = context.factory.create_resource_service()

    for document in documents:
        await resource_service.upsert_document(document)
        logger.debug(
            "Upserted document",
            document_id=document.id,
            title=document.title,
        )

    await context.session.commit()

    # Retrieve the documents from the database to return them with all
    # fields populated
    retrieved_documents = []
    for document in documents:
        retrieved_doc = await resource_service.get_resource_by_id(
            document.id,
        )
        if retrieved_doc is not None:
            if not isinstance(retrieved_doc, Document):
                logger.warning(
                    "Retrieved resource is not a Document",
                    document_id=document.id,
                    resource_class=retrieved_doc.resource_class,
                )
                continue
            retrieved_documents.append(retrieved_doc)
        else:
            logger.warning(
                "Failed to retrieve document after upsert",
                document_id=document.id,
            )

    logger.info(
        "Successfully ingested documents.",
        ingested_count=len(retrieved_documents),
    )

    return [
        DocumentResource.from_domain(doc, request=context.request)
        for doc in retrieved_documents
    ]
