"""The /glossary endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from safir.models import ErrorModel

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency

from .models import SearchedTerm

router = APIRouter(
    prefix=f"{config.path_prefix}/glossary",
    tags=["glossary"],
)


@router.get(
    "/search",
    summary="Search the glossary",
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def search_glossary(
    *,
    search: Annotated[
        str,
        Query(
            title="Search term",
            description=(
                "The term to search for in the glossary. To consider "
                "the definition in the search, also set "
                "`?search_definitions=true`."
            ),
            min_length=1,
            alias="q",
        ),
    ],
    glossary_contexts: Annotated[
        list[str] | None,
        Query(
            title="Contexts",
            alias="context",
            description=(
                "The contexts to search in. If not provided, all contexts "
                "are searched."
            ),
        ),
    ] = None,
    include_abbr: Annotated[
        bool,
        Query(
            title="Include abbreviations",
            description=(
                "Whether to include abbreviations in the results. "
                "Defaults to true. See also `include_terms`."
            ),
        ),
    ] = True,
    include_terms: Annotated[
        bool,
        Query(
            title="Include terms",
            description=(
                "Whether to include glossary terms in the search. Defaults to "
                "true. To get only abbreviations, set `?include_terms=false` "
                "See also `include_abbr`."
            ),
        ),
    ] = True,
    search_definitions: Annotated[
        bool,
        Query(
            title="Search definitions",
            description=(
                "Whether to search in the text of definitions as well. "
                "Defaults to false."
            ),
        ),
    ] = False,
    limit: Annotated[
        int,
        Query(
            title="Limit",
            description=("The maximum number of results to return per page."),
            ge=1,
            le=100,
        ),
    ] = 10,
    offset: Annotated[
        int,
        Query(
            title="Offset",
            description=("The number of results to skip for pagination."),
            ge=0,
        ),
    ] = 0,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> list[SearchedTerm]:
    async with context.session.begin():
        glossary_service = context.factory.create_glossary_service()
        results = await glossary_service.search(
            search_term=search,
            include_abbr=include_abbr,
            include_terms=include_terms,
            search_definitions=search_definitions,
            contexts=glossary_contexts,
            limit=limit,
            offset=offset,
        )
        return [SearchedTerm.from_domain(term) for term in results]
