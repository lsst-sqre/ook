"""Endpoints for the /ook/intersphinx APIs."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency
from ook.exceptions import UpstreamInventoryError

router = APIRouter(
    prefix=f"{config.path_prefix}/intersphinx", tags=["intersphinx"]
)
"""FastAPI router for all intersphinx inventory cache handlers."""


@router.get(
    "/inventory",
    summary="Get a cached intersphinx inventory",
    description=(
        "Serve a cached Sphinx ``objects.inv`` inventory keyed by its"
        " origin URL. On a cache miss the origin is fetched"
        " synchronously, stored, and served. The response carries the"
        " stored content type and an ``Age`` header giving the seconds"
        " since the inventory was fetched from the origin. A cold-miss"
        " upstream failure returns a 502 and is negatively cached. This"
        " endpoint is write-protected by Gafaelfawr at the ingress."
    ),
    response_class=Response,
    responses={
        200: {
            "content": {"application/octet-stream": {}},
            "description": "The cached inventory bytes.",
        },
        502: {"description": "The origin inventory could not be fetched."},
    },
)
async def get_intersphinx_inventory(
    *,
    url: Annotated[
        str,
        Query(
            title="Inventory URL",
            description="The full origin ``objects.inv`` URL to serve.",
            examples=["https://www.sphinx-doc.org/en/master/objects.inv"],
        ),
    ],
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> Response:
    """Serve a cached intersphinx inventory, fetching on a cache miss."""
    service = context.factory.create_intersphinx_cache_service()
    try:
        inventory = await service.get_inventory(url)
    except UpstreamInventoryError:
        # The service wrote a negative-cache row before raising; commit it
        # so the failure is actually cached even though the client gets a
        # 502. The handler-managed transaction would otherwise roll it back.
        await context.session.commit()
        raise
    await context.session.commit()

    age = 0
    if inventory.date_fetched is not None:
        age = max(
            0,
            int(
                (datetime.now(tz=UTC) - inventory.date_fetched).total_seconds()
            ),
        )
    return Response(
        content=inventory.content,
        media_type=inventory.content_type or "application/octet-stream",
        headers={"Age": str(age)},
    )
