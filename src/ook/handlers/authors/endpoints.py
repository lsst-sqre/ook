"""The /authors endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from safir.models import ErrorLocation, ErrorModel

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency
from ook.domain.authors import Orcid
from ook.exceptions import ConflictingQueryParametersError, NotFoundError
from ook.handlers.authors.models import Author, AuthorSearchResult
from ook.storage.authorstore import AuthorsCursor, AuthorSearchCursor

router = APIRouter(
    prefix=f"{config.path_prefix}/authors",
    tags=["authors"],
)

_ORCID_CONFLICTS = {
    "search": (
        "an ORCID matches one record exactly while a search scores fuzzy "
        "name matches, and the two modes return different models"
    ),
    "cursor": (
        "an ORCID identifies at most one author, so the lookup has no pages "
        "to walk"
    ),
}
"""Query parameters that cannot be combined with ``orcid``, each mapped to
the reason no single response could honour both."""


def _reject_orcid_conflicts(**parameters: str | None) -> None:
    """Refuse an ORCID lookup that also carries a parameter it cannot honour.

    Parameters
    ----------
    **parameters
        The conflicting parameters as the request supplied them, keyed by
        name. A parameter that is `None` was not sent and does not conflict.

    Raises
    ------
    ConflictingQueryParametersError
        Raised if any parameter was supplied, located at ``orcid`` so that a
        client sees one error surface for every bad request to that
        parameter.
    """
    for name, value in parameters.items():
        if value is None:
            continue
        raise ConflictingQueryParametersError(
            f"The orcid and {name} query parameters are mutually exclusive: "
            f"{_ORCID_CONFLICTS[name]}. Send only one of them.",
            location=ErrorLocation.query,
            field_path=["orcid"],
        )


@router.get(
    "",
    summary="Get authors",
    description=r"""
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

## Look up by ORCID

Use the `orcid` parameter to resolve an author's ORCID to their record. An
ORCID identifies at most one author, so the response is an array of zero or
one authors — an ORCID nobody holds is an empty array, not a `404`. Entries
carry no `score` field: this is an exact lookup, not a search.

### Accepted forms

The ORCID may be written bare (`0000-0003-3001-676X`), with a lowercase
checksum character, or as an `orcid.org` URL, with or without an
`https://`/`http://` scheme, a `www.` prefix, or a trailing slash.
Surrounding whitespace is ignored. Every form is normalized to the bare
uppercase identifier before the lookup.

### Rejected requests

These are each a `422` whose error is located at `["query", "orcid"]`:

- A value that is not an ORCID — a URL on a host other than `orcid.org`,
  the hyphen-less 16-character compact form, or anything else that does not
  match `\d{4}-\d{4}-\d{4}-\d{3}[0-9X]` once normalized.
- A well-formed identifier whose ISO 7064 mod-11-2 check digit does not
  verify.
- `orcid` sent together with `search`. The two modes return different models
  under different match semantics, so there is no response that honours both;
  dropping one silently would hide a client bug.
- `orcid` sent together with `cursor`. An ORCID matches at most one record,
  so there are no pages to walk.

### No pagination

The ORCID lookup returns neither a `Link` nor an `X-Total-Count` header, for
the same reason `cursor` is refused. `limit` always carries a default and so
cannot be told apart from an absent one; it is accepted and has no effect on
this path.

## Pagination

The regular listing and search modes support cursor-based pagination (the
ORCID lookup does not):

- Use `cursor` parameter to navigate through pages
- `limit` parameter controls page size (1-100, default 100)
- Response includes `Link` header with next/prev URLs
- `X-Total-Count` header provides total result count
    """,
)
async def get_authors(
    *,
    orcid: Annotated[
        Orcid | None,
        Query(
            title="ORCID",
            description=(
                "ORCID of an author, as the bare identifier or an orcid.org "
                "URL. Returns zero or one authors. Cannot be combined with "
                "`search` or `cursor`."
            ),
            examples=["0000-0003-3001-676X"],
        ),
    ] = None,
    search: Annotated[
        str | None,
        Query(
            title="Search query",
            description=(
                "Fuzzy search query for author names. Cannot be combined "
                "with `orcid`."
            ),
            min_length=2,
        ),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(
            title="Pagination cursor",
            description=(
                "Cursor to navigate paginated results. Cannot be combined "
                "with `orcid`, which never paginates."
            ),
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            title="Row limit",
            description=(
                "Maximum number of entries to return. Ignored when `orcid` "
                "is given, which returns at most one author."
            ),
            examples=[100],
            ge=1,
            le=100,
        ),
    ] = 100,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> list[Author] | list[AuthorSearchResult]:
    if orcid:
        _reject_orcid_conflicts(search=search, cursor=cursor)

    async with context.session.begin():
        author_service = context.factory.create_author_service()

        if orcid:
            # Exact lookup by ORCID; at most one author holds any ORCID, so
            # there is nothing to paginate and no Link/X-Total-Count header.
            author = await author_service.get_author_by_orcid(orcid)
            return [Author.from_domain(author)] if author else []

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
