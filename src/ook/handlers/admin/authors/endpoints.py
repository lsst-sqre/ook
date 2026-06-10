"""Admin endpoints for author management."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response
from safir.models import ErrorModel

from ook.dependencies.context import RequestContext, context_dependency
from ook.exceptions import ConflictError, NotFoundError
from ook.handlers.admin.authors.models import AuthorAlias, AuthorAliasRequest

router = APIRouter(
    prefix="/authors",
    tags=["admin", "authors"],
)


@router.get(
    "/aliases",
    summary="List author ID aliases",
    description=(
        "List all author internal ID aliases and the root author IDs they "
        "resolve to."
    ),
)
async def get_author_aliases(
    *,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> list[AuthorAlias]:
    async with context.session.begin():
        author_service = context.factory.create_author_service()
        aliases = await author_service.get_author_aliases()
        return [AuthorAlias.from_domain(alias) for alias in aliases]


@router.post(
    "/aliases",
    summary="Create an author ID alias",
    description="""
Create an alias for an author's internal ID. Requests for the alias through
`GET /ook/authors/{internal_id}` resolve to the root author's record, and
documents that reference the alias are attributed to the root author. The
author listing and search endpoints don't show aliases.

Aliases preserve backwards compatibility when two authordb.yaml IDs
correspond to the same person: register the superseded ID as an alias of
the canonical one. During lsst-texmf ingest, an authordb.yaml entry whose
key is a registered alias is skipped, so the root author keeps a unique
ORCID.

## Merging

If an author record already exists with the alias's internal ID, it is
merged into the root author: contributor (resource attribution) records
pointing to it are re-pointed to the root author and the duplicate author
record is deleted. **This is a destructive operation** that cannot be
undone.

If the requested root author ID is itself an alias, it is resolved so that
the new alias points directly to the root author (alias chains never form).
    """,
    status_code=201,
    responses={
        201: {"description": "Alias created"},
        404: {"description": "Root author not found", "model": ErrorModel},
        409: {
            "description": (
                "Alias conflicts with the root author ID or an existing alias"
            ),
            "model": ErrorModel,
        },
    },
)
async def create_author_alias(
    *,
    alias_request: AuthorAliasRequest,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> AuthorAlias:
    async with context.session.begin():
        author_service = context.factory.create_author_service()

        # Resolve the requested root author through any existing aliases so
        # that alias chains flatten to the root author.
        root_author = await author_service.get_author_by_id(
            alias_request.author_internal_id
        )
        if root_author is None:
            raise NotFoundError(
                message=(
                    f"Author {alias_request.author_internal_id!r} not found"
                ),
            )

        alias = await author_service.create_author_alias(
            internal_id=alias_request.internal_id,
            author_internal_id=root_author.internal_id,
        )

        await context.session.commit()

        return AuthorAlias.from_domain(alias)


@router.delete(
    "/aliases/{internal_id}",
    summary="Delete an author ID alias",
    description=(
        "Delete an author internal ID alias. The root author is unaffected. "
        "Note that if the alias is still present in authordb.yaml with a "
        "duplicate ORCID, the next lsst-texmf ingest will fail."
    ),
    status_code=204,
    responses={
        204: {"description": "Alias successfully deleted"},
        404: {"description": "Alias not found", "model": ErrorModel},
    },
)
async def delete_author_alias(
    *,
    internal_id: Annotated[
        str,
        Path(
            title="Alias internal ID",
            description="The alias internal ID",
            examples=["marshallpj"],
        ),
    ],
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> Response:
    async with context.session.begin():
        author_service = context.factory.create_author_service()

        deleted = await author_service.delete_author_alias(internal_id)
        if not deleted:
            raise NotFoundError(
                message=f"Author alias {internal_id!r} not found",
            )

        await context.session.commit()

    return Response(status_code=204)


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
        if author.internal_id != internal_id:
            # The ID resolved through an alias to a different author
            raise ConflictError(
                message=(
                    f"{internal_id!r} is an alias of author "
                    f"{author.internal_id!r}. Delete the alias with "
                    f"DELETE /ook/admin/authors/aliases/{internal_id} "
                    "instead."
                ),
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
