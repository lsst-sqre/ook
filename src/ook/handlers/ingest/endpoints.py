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
    DocumentIngestResult,
    DocumentIngestStatus,
    LsstTexmfIngestRequest,
    LtdIngestRequest,
    SdmSchemasIngestRequest,
)

router = APIRouter(prefix=f"{config.path_prefix}/ingest", tags=["ingest"])
"""FastAPI router for all ingest handlers."""


def _sanitize_error_detail(exc: Exception) -> str:
    """Build a per-item error detail that does not leak SQL or parameters.

    SQLAlchemy appends ``[SQL: ...]`` and ``[parameters: ...]`` sections to
    its exception strings, which would otherwise surface the statement and
    bound values in the API response. This keeps only the exception class
    name and the first line of the message up to that ``[SQL:`` marker. The
    full exception is still logged via ``logger.exception``.

    Parameters
    ----------
    exc
        The exception raised while ingesting a single document.

    Returns
    -------
    str
        A sanitized, single-line error detail.
    """
    message = str(exc).split("[SQL:", 1)[0].split("\n", 1)[0].strip()
    return f"{type(exc).__name__}: {message}"


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
) -> list[DocumentIngestResult]:
    """Ingest document resources into the bibliography database.

    Each submitted document is ingested independently and reported with a
    per-item status: ``created`` when a new resource is minted, ``updated``
    when an existing resource is matched by natural key, or ``failed`` (with
    error detail) when the document could not be ingested. Results are
    returned in request order.
    """
    logger = context.logger
    logger.info(
        "Received request to ingest documents.",
        document_count=len(ingest_request.documents),
    )

    # The storage layer resolves each document to an existing row by natural
    # key or mints a new time-ordered ID, reporting which it did via the
    # upsert result's ``created`` flag.
    resource_service = context.factory.create_resource_service()

    results: list[DocumentIngestResult] = []
    for doc_request in ingest_request.documents:
        document = doc_request.to_domain()
        try:
            # Each document runs in its own savepoint so one failure rolls
            # back only that document and leaves the batch transaction usable.
            # The whole result — including the retrieval and response
            # construction — stays inside the savepoint, so any failure here
            # rolls the document's writes back and reports ``failed`` truly.
            async with context.session.begin_nested():
                upsert_result = await resource_service.upsert_document(
                    document
                )
                resource_id = upsert_result.resource_id
                retrieved = await resource_service.get_resource_by_id(
                    resource_id
                )
                if not isinstance(retrieved, Document):
                    raise TypeError(
                        f"Upserted resource {resource_id} is not a document"
                    )
                status = (
                    DocumentIngestStatus.created
                    if upsert_result.created
                    else DocumentIngestStatus.updated
                )
                result = DocumentIngestResult(
                    handle=document.handle,
                    status=status,
                    resource=DocumentResource.from_domain(
                        retrieved, request=context.request
                    ),
                )
            logger.debug(
                "Ingested document",
                document_id=resource_id,
                handle=document.handle,
                status=status.value,
            )
        except Exception as exc:
            logger.exception(
                "Failed to ingest document",
                handle=document.handle,
                title=document.title,
            )
            result = DocumentIngestResult(
                handle=document.handle,
                status=DocumentIngestStatus.failed,
                error=_sanitize_error_detail(exc),
            )
        results.append(result)

    await context.session.commit()

    logger.info(
        "Completed document ingest.",
        created=sum(
            1 for r in results if r.status is DocumentIngestStatus.created
        ),
        updated=sum(
            1 for r in results if r.status is DocumentIngestStatus.updated
        ),
        failed=sum(
            1 for r in results if r.status is DocumentIngestStatus.failed
        ),
    )

    return results
