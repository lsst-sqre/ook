"""The /authors endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from safir.models import ErrorModel

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency
from ook.exceptions import NotFoundError
from ook.handlers.authors.models import Author, AuthorSearchResult
from ook.storage.authorstore import AuthorsCursor, AuthorSearchCursor

router = APIRouter(
    prefix=f"{config.path_prefix}/authors",
    tags=["authors"],
)


@router.get(
    "",
    summary="Get authors",
    description="""
Get a list of authors from the [lsst-texmf
authordb.yaml](https://github.com/lsst/lsst-texmf/blob/main/authordb.yaml)
database.

## Search by name

Use the `search` parameter to for flexible and typo-tolerant searches of
authors by name.

### Name formats

The search system automatically detects and handles various name formats:

- "Last, First"
- "Last, Initial"
- "First Last"
- Family name only
- Given name only
- Compound family names
- Names with suffixes

### Relevance scoring

Search results include a `score` field (0-100) indicating match quality:

- **90-100**: Exact or near-exact matches
- **70-89**: Good matches with minor variations
- **50-69**: Partial matches or fuzzy matches
- **1-49**: Weak matches (rare, usually filtered out)

Results are automatically sorted by relevance score in descending order.

## Pagination

Both regular listing and search results support cursor-based pagination:

- Use `cursor` parameter to navigate through pages
- `limit` parameter controls page size (1-100, default 100)
- Response includes `Link` header with next/prev URLs
- `X-Total-Count` header provides total result count
    """,
)
async def get_authors(
    *,
    search: Annotated[
        str | None,
        Query(
            title="Search query",
            description="Fuzzy search query for author names",
            min_length=2,
        ),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(
            title="Pagination cursor",
            description="Cursor to navigate paginated results",
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            title="Row limit",
            description="Maximum number of entries to return",
            examples=[100],
            ge=1,
            le=100,
        ),
    ] = 100,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> list[Author] | list[AuthorSearchResult]:
    async with context.session.begin():
        author_service = context.factory.create_author_service()

        if search:
            # Perform fuzzy search
            search_results = await author_service.search_authors(
                search_query=search,
                limit=limit,
                cursor=AuthorSearchCursor.from_str(cursor) if cursor else None,
            )
            response = context.response
            request = context.request
            response.headers["Link"] = search_results.link_header(request.url)
            response.headers["X-Total-Count"] = str(search_results.count)
            return [
                AuthorSearchResult.from_domain(result)
                for result in search_results.entries
            ]
        else:
            # Get all authors (existing functionality)
            author_results = await author_service.get_authors(
                limit=limit,
                cursor=AuthorsCursor.from_str(cursor) if cursor else None,
            )
            if author_results.count == 0:
                raise NotFoundError(
                    message="No authors found",
                )
            if cursor or limit:
                response = context.response
                request = context.request
                response.headers["Link"] = author_results.link_header(
                    request.url
                )
                response.headers["X-Total-Count"] = str(author_results.count)
            return [
                Author.from_domain(author) for author in author_results.entries
            ]


@router.get(
    "/{internal_id}",
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
    data, like emails are not available through this endpoint.
    """
    async with context.session.begin():
        author_service = context.factory.create_author_service()
        author = await author_service.get_author_by_id(internal_id)
        if author is None:
            raise NotFoundError(
                message=f"Author {internal_id!r} not found",
            )
        return Author.from_domain(author)
