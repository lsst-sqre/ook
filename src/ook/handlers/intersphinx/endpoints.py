"""Endpoints for the /ook/intersphinx APIs."""

import hashlib
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency
from ook.exceptions import UpstreamInventoryError

router = APIRouter(
    prefix=f"{config.path_prefix}/intersphinx", tags=["intersphinx"]
)
"""FastAPI router for all intersphinx inventory cache handlers."""


def _strip_weak_prefix(etag: str) -> str:
    """Strip an optional ``W/`` weakness prefix from an entity-tag."""
    if etag.startswith("W/"):
        return etag[2:]
    return etag


def _if_none_match_matches(header_value: str, current_etag: str) -> bool:
    """Return whether an ``If-None-Match`` header matches the current ETag.

    Uses RFC 9110 weak comparison: the ``W/`` weakness prefix is ignored on
    both sides and the remaining opaque-tags are compared verbatim. The
    header may carry a comma-separated list of validators, and ``*`` matches
    any current representation.
    """
    candidates = [token.strip() for token in header_value.split(",")]
    if "*" in candidates:
        return True
    normalized_current = _strip_weak_prefix(current_etag)
    return any(
        _strip_weak_prefix(candidate) == normalized_current
        for candidate in candidates
        if candidate
    )


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
        " endpoint is protected by Gafaelfawr at the ingress."
    ),
    response_class=Response,
    responses={
        200: {
            "content": {"application/octet-stream": {}},
            "description": "The cached inventory bytes.",
        },
        304: {
            "description": (
                "The client's ``If-None-Match`` validator matches the"
                " currently-cached inventory; no body is returned."
            )
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
    if_none_match: Annotated[
        str | None,
        Header(
            description=(
                "A conditional-request validator. When it matches the"
                " currently-cached inventory's ``ETag`` (RFC 9110 weak"
                " comparison; ``*`` matches any cached representation), the"
                " endpoint responds ``304 Not Modified`` with no body."
            ),
        ),
    ] = None,
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
    # A strong ETag identifying the bytes Ook currently serves: the quoted
    # SHA-256 hex digest of the served content (RFC 9110). It is hashed per
    # request over the 100-500 KB body; a stored digest is a later
    # optimization. This is distinct from ``inventory.etag``, which is the
    # origin's upstream validator.
    content = inventory.content or b""
    etag = f'"{hashlib.sha256(content).hexdigest()}"'

    # Conditional-request handling: when the client already holds the
    # currently-cached representation, revalidate cheaply with a bodyless 304
    # that carries only the ETag (not the Age-bearing 200 response shape).
    if if_none_match is not None and _if_none_match_matches(
        if_none_match, etag
    ):
        return Response(status_code=304, headers={"ETag": etag})

    return Response(
        content=inventory.content,
        media_type=inventory.content_type or "application/octet-stream",
        headers={"Age": str(age), "ETag": etag},
    )
