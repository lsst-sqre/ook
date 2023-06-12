"""Handlers for the app's external root endpoints, ``/ook/``."""


from fastapi import APIRouter, Request, Response
from pydantic import AnyHttpUrl
from safir.metadata import get_metadata

from ook.config import config

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
) -> Response:
    """Trigger an ingest of a project in LSST the Docs."""
    return Response(status_code=501)
