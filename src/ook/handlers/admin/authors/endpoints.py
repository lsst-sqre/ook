"""Admin endpoints for author management."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response
from safir.models import ErrorModel

from ook.dependencies.context import RequestContext, context_dependency
from ook.exceptions import NotFoundError

router = APIRouter(
    prefix="/authors",
    tags=["admin", "authors"],
)


@router.delete(
    "/{internal_id}",
    summary="Delete author by internal ID",
    description="""
Delete an author from the database by their internal ID.

**This is a destructive operation** that will permanently remove the author
and their affiliation associations from the database. This endpoint is
typically used to clean up stale author entries detected during ingest.

## Cascading Behavior

When an author is deleted:

- All author-affiliation relationships are automatically deleted
- The affiliations themselves are preserved (they may be used by other authors)

## Use Cases

- Removing stale author entries after ID changes in lsst-texmf
- Cleaning up duplicate or incorrect author records
- Manual database maintenance operations
    """,
    status_code=204,
    responses={
        204: {"description": "Author successfully deleted"},
        404: {"description": "Author not found", "model": ErrorModel},
    },
)
async def delete_author(
    *,
    internal_id: Annotated[
        str,
        Path(
            title="Internal ID",
            description="The internal ID from lsst/lsst-texmf's authordb.yaml",
            examples=["sickj"],
        ),
    ],
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> Response:
    """Delete an author by their internal ID.

    This operation is permanent and cannot be undone. The author and all
    their affiliation associations will be removed from the database.
    """
    async with context.session.begin():
        author_service = context.factory.create_author_service()

        # Verify the author exists before attempting deletion
        author = await author_service.get_author_by_id(internal_id)
        if author is None:
            raise NotFoundError(
                message=f"Author {internal_id!r} not found",
            )

        # Log the deletion with author details
        author_name = (
            f"{author.given_name + ' ' if author.given_name else ''}"
            f"{author.surname}"
        )
        context.logger.info(
            "Deleting author",
            internal_id=internal_id,
            author_name=author_name,
            orcid=author.orcid,
        )

        # Perform the deletion
        await author_service.delete_author(internal_id)

        await context.session.commit()

        context.logger.info(
            "Author deleted successfully",
            internal_id=internal_id,
        )

    return Response(status_code=204)
