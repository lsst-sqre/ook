"""Endpoints for /ook/links/ APIs."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from safir.models import ErrorModel

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency
from ook.exceptions import NotFoundError
from ook.storage.linkstore import (
    SdmColumnLinksCollectionCursor,
    SdmLinksCollectionCursor,
    SdmTableLinksCollectionCursor,
)

from .models import Link, SdmDomainInfo, SdmLinks

router = APIRouter(prefix=f"{config.path_prefix}/links", tags=["links"])
"""FastAPI router for the links API."""

# Common path parameters

schema_name_path = Annotated[
    str, Path(title="Schema name", examples=["dp02_dc2_catalogs"])
]

table_name_path = Annotated[str, Path(title="Table name", examples=["Object"])]

column_name_path = Annotated[
    str, Path(title="Column name", examples=["detect_isPrimary"])
]


@router.get(
    "/domains/sdm",
    summary="Information about the SDM domain",
    response_description="Information about the SDM domain",
)
async def get_sdm_domain_info(
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> SdmDomainInfo:
    """Get information about the SDM domain."""
    return SdmDomainInfo.create(request=context.request)


@router.get(
    "/domains/sdm/schemas",
    summary="List SDM schemas' doc links",
    response_description="List of SDM schemas and their doc links",
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def get_sdm_links(
    *,
    include_tables: Annotated[
        bool,
        Query(
            title="Include tables",
            description="Whether to include tables in the response",
        ),
    ] = False,
    include_columns: Annotated[
        bool,
        Query(
            title="Include columns",
            description="Whether to include columns in the response",
        ),
    ] = False,
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
) -> list[SdmLinks]:
    if cursor:
        parsed_cursor = SdmLinksCollectionCursor.from_str(cursor)
    else:
        parsed_cursor = None

    async with context.session.begin():
        link_service = context.factory.create_links_service()
        results = await link_service.get_links_for_sdm_collection(
            include_schemas=True,
            include_tables=include_tables,
            include_columns=include_columns,
            limit=limit,
            cursor=parsed_cursor,
        )
        if results.count == 0:
            raise NotFoundError("No links found for SDM schemas.")
        if cursor or limit:
            response = context.response
            request = context.request
            response.headers["Link"] = results.link_header(request.url)
            response.headers["X-Total-Count"] = str(results.count)
        return SdmLinks.from_sdm_links_collection(
            sdm_links_collections=results.entries, request=context.request
        )


@router.get(
    "/domains/sdm/schemas/{schema_name}",
    summary="Get an SDM schemas's doc links",
    response_description="List of doc links for an SDM schema",
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def get_sdm_schema_links(
    schema_name: schema_name_path,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> list[Link]:
    logger = context.logger
    logger.debug(
        "Received request to get documentation links for an SDM schema.",
        schema_name=schema_name,
    )
    async with context.session.begin():
        link_service = context.factory.create_links_service()
        links = await link_service.get_links_for_sdm_schema(schema_name)
        if links is None:
            raise NotFoundError(
                f"No links found for SDM schema {schema_name}."
            )
        return [Link.from_domain_link(link) for link in links]


@router.get(
    "/domains/sdm/schemas/{schema_name}/tables",
    summary="List SDM tables' doc links scoped to a schema",
    response_description="List of SDM tables and columns and their doc links",
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def get_sdm_links_scoped_to_schema(
    *,
    include_columns: Annotated[
        bool,
        Query(title="Include columns"),
    ] = False,
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
    schema_name: schema_name_path,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> list[SdmLinks]:
    async with context.session.begin():
        link_service = context.factory.create_links_service()

        if include_columns:
            results = await link_service.get_links_for_sdm_collection(
                include_schemas=False,
                include_tables=True,
                include_columns=True,
                schema_name=schema_name,
                limit=limit,
                cursor=SdmLinksCollectionCursor.from_str(cursor)
                if cursor
                else None,
            )
            if results.count == 0:
                raise NotFoundError(
                    f"No links found for SDM tables in schema {schema_name!r}."
                )
            if cursor or limit:
                response = context.response
                request = context.request
                response.headers["Link"] = results.link_header(request.url)
                response.headers["X-Total-Count"] = str(results.count)
            return SdmLinks.from_sdm_links_collection(
                sdm_links_collections=results.entries, request=context.request
            )

        else:
            table_results = await link_service.get_table_links_for_sdm_schema(
                schema_name=schema_name,
                limit=limit,
                cursor=SdmTableLinksCollectionCursor.from_str(cursor)
                if cursor
                else None,
            )

            if table_results.count == 0:
                raise NotFoundError(
                    f"No links found for SDM tables in schema {schema_name!r}."
                )
            if cursor or limit:
                response = context.response
                request = context.request
                response.headers["Link"] = table_results.link_header(
                    request.url
                )
                response.headers["X-Total-Count"] = str(table_results.count)

            return SdmLinks.from_domain(
                domain_collection=table_results.entries,
                request=context.request,
            )


@router.get(
    "/domains/sdm/schemas/{schema_name}/tables/{table_name}",
    summary="Get an SDM table's doc links",
    response_description="List of doc links for an SDM table",
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def get_sdm_schema_table_links(
    schema_name: schema_name_path,
    table_name: table_name_path,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> list[Link]:
    logger = context.logger
    logger.debug(
        "Received request to get documentation links for an SDM table.",
        schema_name=schema_name,
        table_name=table_name,
    )
    async with context.session.begin():
        link_service = context.factory.create_links_service()
        links = await link_service.get_links_for_sdm_table(
            schema_name=schema_name, table_name=table_name
        )
        if links is None:
            raise NotFoundError(
                f"No links found for SDM table {table_name} in "
                f"schema {schema_name}."
            )
        return [Link.from_domain_link(link) for link in links]


@router.get(
    "/domains/sdm/schemas/{schema_name}/tables/{table_name}/columns",
    summary="List SDM columns' doc links for a table",
    response_description="List of SDM columns and their doc links",
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def get_sdm_schema_column_links_for_table(
    *,
    schema_name: schema_name_path,
    table_name: table_name_path,
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
) -> list[SdmLinks]:
    if cursor:
        parsed_cursor = SdmColumnLinksCollectionCursor.from_str(cursor)
    else:
        parsed_cursor = None

    async with context.session.begin():
        link_service = context.factory.create_links_service()
        results = await link_service.get_links_for_sdm_columns_in_table(
            schema_name=schema_name,
            table_name=table_name,
            cursor=parsed_cursor,
            limit=limit,
        )
        if results.count == 0:
            raise NotFoundError(
                f"No links found for SDM columns in table "
                f"{table_name} in schema {schema_name}."
            )
        if cursor or limit:
            response = context.response
            request = context.request
            response.headers["Link"] = results.link_header(request.url)
            response.headers["X-Total-Count"] = str(results.count)
        return SdmLinks.from_domain(
            domain_collection=results.entries, request=context.request
        )


@router.get(
    "/domains/sdm/schemas/{schema_name}/tables/{table_name}/columns/{column_name}",
    summary="Get an SDM column's doc links",
    response_description="List of doc links for an SDM column",
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def get_sdm_schema_column_links(
    schema_name: schema_name_path,
    table_name: table_name_path,
    column_name: column_name_path,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> list[Link]:
    logger = context.logger
    logger.debug(
        "Received request to get documentation links for an SDM column.",
        schema_name=schema_name,
        table_name=table_name,
        column_name=column_name,
    )
    async with context.session.begin():
        link_service = context.factory.create_links_service()
        links = await link_service.get_links_for_sdm_column(
            schema_name=schema_name,
            table_name=table_name,
            column_name=column_name,
        )
        if links is None:
            raise NotFoundError(
                f"No links found for SDM column {column_name} in table "
                f"{table_name} in schema {schema_name}."
            )
        return [Link.from_domain_link(link) for link in links]
