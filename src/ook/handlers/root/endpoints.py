"""Handlers for the app's external root endpoints, ``/ook/``."""

from fastapi import APIRouter, Request
from pydantic import AnyHttpUrl
from safir.metadata import get_metadata

from ook.config import config

from .models import IndexResponse

__all__ = ["get_index", "router"]

router = APIRouter(prefix=config.path_prefix)
"""FastAPI router for all external handlers."""


@router.get(
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
