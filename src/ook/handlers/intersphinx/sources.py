"""Endpoints for the /ook/intersphinx/sources registry API.

The registry names the documentation sites Ook pulls ``objects.inv``
inventories from. Its write operations are gated by the ``exec:admin``
scope at the Gafaelfawr ingress -- deliberately not the
``write:intersphinx`` scope that warms the inventory cache, since
registering a site commits Ook to serving links from it, while warming the
cache only spends a fetch. As everywhere else in Ook, that gate is Phalanx
configuration rather than in-app code; the scope is named in each
endpoint's description so the published API says what the ingress
enforces.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response
from safir.models import ErrorModel

from ook.dependencies.context import RequestContext, context_dependency
from ook.domain.base32id import Base32Id, serialize_ook_base32_id
from ook.exceptions import NotFoundError

from .models import (
    IntersphinxSource,
    IntersphinxSourceRequest,
    IntersphinxSourceUpdateRequest,
)

router = APIRouter(prefix="/sources", tags=["intersphinx"])
"""FastAPI router for the intersphinx source registry."""

ADMIN_SCOPE_NOTE = (
    "This endpoint is write-protected by Gafaelfawr at the ingress and"
    " requires the ``exec:admin`` scope -- not the ``write:intersphinx``"
    " scope that warms the inventory cache."
)
"""The sentence every write endpoint's description ends with."""

OBSERVABILITY_NOTE = (
    "The observability fields (``date_ingested``, ``last_status``,"
    " ``last_error``) are read-only: they report the most recent ingest"
    " attempt and are written by ingest runs, never by this API."
)
"""The sentence describing the fields a client cannot write."""

SourceIdPath = Annotated[
    Base32Id,
    Path(
        title="Source ID",
        description=(
            "The Crockford Base32 identifier of the registered source."
        ),
        examples=["1234-5678-90ab-cd2f"],
    ),
]
"""The registration ID a single-source route is addressed by.

Carried in the URL as the Base32 string the registration publishes, so the
checksum it holds makes a mistyped ID a ``422`` rather than a lookup that
quietly misses.
"""


def _source_not_found(source_id: int) -> NotFoundError:
    """Build the 404 for a registration ID nothing answers to.

    The ID is rendered back in the Base32 form the client addressed the
    route with, so the message names the identifier the client holds rather
    than the integer it decodes to.
    """
    return NotFoundError(
        message=(
            f"Intersphinx source {serialize_ook_base32_id(source_id)} not"
            " found"
        )
    )


@router.post(
    "",
    summary="Register a documentation source",
    description=(
        "Register a documentation site for intersphinx ingest by the URL"
        " of the ``objects.inv`` inventory it publishes, together with a"
        " human title. The title surfaces as the ``collection_title`` of"
        " every link ingested from the site, so it is what a reader sees"
        " naming where a link goes."
        "\n\n"
        "The inventory URL is the registration's identity: registering one"
        " that is already registered is a conflict rather than a second"
        " row. Only the canonical version of a site is registered — there"
        " is no version dimension here, because a registration is a place"
        " to send readers to rather than a build to archive."
        "\n\n"
        "A source is registered enabled unless ``enabled`` says otherwise."
        " Disabling is not deleting: a disabled source keeps its"
        " registration and its links, so a site can be parked without"
        " losing the links Ook already serves from it."
        f"\n\n{OBSERVABILITY_NOTE} A freshly registered source has all"
        " three null, which is what makes it read as pending rather than"
        " healthy."
        f"\n\n{ADMIN_SCOPE_NOTE}"
    ),
    status_code=201,
    responses={
        201: {"description": "The source was registered."},
        409: {
            "description": "The inventory URL is already registered.",
            "model": ErrorModel,
        },
        422: {"description": "Invalid registration", "model": ErrorModel},
    },
)
async def register_intersphinx_source(
    *,
    source_request: IntersphinxSourceRequest,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> IntersphinxSource:
    """Register a documentation source for intersphinx ingest."""
    async with context.session.begin():
        service = context.factory.create_intersphinx_source_service()
        source = await service.register_source(
            url=str(source_request.url),
            title=source_request.title,
            enabled=source_request.enabled,
        )
        registration = IntersphinxSource.from_domain(
            source, request=context.request
        )
    context.response.headers["Location"] = registration.self_url
    return registration


@router.get(
    "",
    summary="List registered documentation sources",
    description=(
        "List the documentation sites registered for intersphinx ingest,"
        " ordered by inventory URL. Ordered by URL rather than by"
        " registration ID so the listing is stable across a delete and"
        " re-register of the same site."
        f"\n\n{OBSERVABILITY_NOTE} Reading them across the listing is how"
        " an operator sees which sites the last ingest run failed on."
    ),
)
async def get_intersphinx_sources(
    *,
    enabled_only: Annotated[
        bool,
        Query(
            title="Only enabled sources",
            description=(
                "List only the sources ingest runs visit, which is what"
                " the next run will consider."
            ),
        ),
    ] = False,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> list[IntersphinxSource]:
    """List the registered documentation sources."""
    async with context.session.begin():
        service = context.factory.create_intersphinx_source_service()
        sources = await service.list_sources(enabled_only=enabled_only)
        return [
            IntersphinxSource.from_domain(source, request=context.request)
            for source in sources
        ]


@router.get(
    "/{source_id}",
    summary="Get a registered documentation source",
    description=(
        "Get one registered documentation site, including when Ook last"
        " attempted to ingest it and how that attempt ended."
        f"\n\n{OBSERVABILITY_NOTE}"
    ),
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def get_intersphinx_source(
    *,
    source_id: SourceIdPath,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> IntersphinxSource:
    """Get one registered documentation source."""
    async with context.session.begin():
        service = context.factory.create_intersphinx_source_service()
        source = await service.get_source(source_id)
        if source is None:
            raise _source_not_found(source_id)
        return IntersphinxSource.from_domain(source, request=context.request)


@router.patch(
    "/{source_id}",
    summary="Update a registered documentation source",
    description=(
        "Change a registered site's inventory URL, title, or enabled flag."
        " Only the fields present in the request body are written, so"
        " retitling a source does not have to restate whether it is"
        " enabled."
        "\n\n"
        "Moving a registration onto an inventory URL another registration"
        " already holds is a conflict, for the same reason registering a"
        " duplicate is."
        f"\n\n{OBSERVABILITY_NOTE} A client that could write them could"
        " make a source claim a success it never had, so they are not"
        " accepted here; re-run the ingest instead."
        f"\n\n{ADMIN_SCOPE_NOTE}"
    ),
    responses={
        404: {"description": "Not found", "model": ErrorModel},
        409: {
            "description": (
                "The inventory URL is already registered to another source."
            ),
            "model": ErrorModel,
        },
        422: {"description": "Invalid update", "model": ErrorModel},
    },
)
async def update_intersphinx_source(
    *,
    source_id: SourceIdPath,
    update_request: IntersphinxSourceUpdateRequest,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> IntersphinxSource:
    """Update a registered documentation source."""
    async with context.session.begin():
        service = context.factory.create_intersphinx_source_service()
        source = await service.update_source(
            source_id,
            url=(
                None if update_request.url is None else str(update_request.url)
            ),
            title=update_request.title,
            enabled=update_request.enabled,
        )
        if source is None:
            raise _source_not_found(source_id)
        return IntersphinxSource.from_domain(source, request=context.request)


@router.delete(
    "/{source_id}",
    summary="Delete a registered documentation source",
    description=(
        "Delete a registration and, by cascade, every link ingested from"
        " it. The entities those links pointed at are left behind: another"
        " source may document the same object, and an entity nothing"
        " documents any more is pruned by the ingest path instead."
        "\n\n"
        "To stop ingesting a site without withdrawing the links Ook"
        " already serves from it, set ``enabled`` to false instead."
        f"\n\n{ADMIN_SCOPE_NOTE}"
    ),
    status_code=204,
    responses={
        204: {"description": "The source was deleted."},
        404: {"description": "Not found", "model": ErrorModel},
    },
)
async def delete_intersphinx_source(
    *,
    source_id: SourceIdPath,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> Response:
    """Delete a registered documentation source."""
    async with context.session.begin():
        service = context.factory.create_intersphinx_source_service()
        deleted = await service.delete_source(source_id)
        if not deleted:
            raise _source_not_found(source_id)
    return Response(status_code=204)
