"""The /authors endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from safir.models import ErrorModel

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency
from ook.exceptions import NotFoundError
from ook.handlers.authors.models import Author, AuthorFull

router = APIRouter(
    prefix=f"{config.path_prefix}/authors",
    tags=["authors"],
)


@router.get(
    "/id/{internal_id}",
    summary="Get author by internal ID",
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def get_author_by_id(
    *,
    internal_id: Annotated[
        str,
        Path(
            title="Internal ID",
            description=(
                "The internal ID from lsst/lsst-texmf's authordb.yaml."
            ),
        ),
    ],
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> Author:
    """This endpoint provides public information about authors. Some known
    data, like emails are not available through this endpoint. Use
    `GET /project-access/id/{internal_id}` to get the full author record.
    """
    async with context.session.begin():
        author_service = context.factory.create_author_service()
        author = await author_service.get_author_by_id(internal_id)
        if author is None:
            raise NotFoundError(
                message=f"Author {internal_id!r} not found",
            )
        return Author.from_domain(author)


@router.get(
    "/project-access/id/{internal_id}",
    summary="Get author by internal ID (full access)",
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def get_full_author_by_id(
    *,
    internal_id: Annotated[
        str,
        Path(
            title="Internal ID",
            description=(
                "The internal ID from lsst/lsst-texmf's authordb.yaml."
            ),
        ),
    ],
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> AuthorFull:
    """*Requires authentication.*

    This endpoint is an authenticated version of
    `GET /authors/id/{internal_id}` and provides additional information
    about authors.
    """
    async with context.session.begin():
        author_service = context.factory.create_author_service()
        author = await author_service.get_author_by_id(internal_id)
        if author is None:
            raise NotFoundError(
                message=f"Author {internal_id!r} not found",
            )
        return AuthorFull.from_domain(author)
