"""Endpoints for /ook/links/ APIs."""

from typing import Annotated

from fastapi import APIRouter, Depends
from safir.models import ErrorModel

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency
from ook.exceptions import NotFoundError

from .models import EntityLinks

router = APIRouter(prefix=f"{config.path_prefix}/links", tags=["links"])
"""FastAPI router for the links API."""


@router.get(
    "/domains/sdm-schemas/schemas",
    summary="List schemas and their documentation links",
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def get_sdm_schema_links_list(
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> list[EntityLinks]:
    """List schemas and their documentation links."""
    logger = context.logger
    logger.info(
        "Received request to list schemas and their documentation links."
    )
    async with context.session.begin():
        link_service = context.factory.create_links_service()
        links = await link_service.list_sdm_schemas()
        return [
            EntityLinks.from_domain_models(
                subject_name=schema_name,
                domain=schema_links,
                self_url=str(
                    context.request.url_for(
                        "get_sdm_schema_links", schema_name=schema_name
                    )
                ),
            )
            for schema_name, schema_links in links
        ]


@router.get(
    "/domains/sdm-schemas/schemas/{schema_name}",
    summary="Get documentation links for a SDM schema",
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def get_sdm_schema_links(
    schema_name: str,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> EntityLinks:
    """Get documentation links for a SDM schema."""
    logger = context.logger
    logger.info(
        "Received request to get documentation links for a SDM schema."
    )
    async with context.session.begin():
        link_service = context.factory.create_links_service()
        links = await link_service.get_links_for_sdm_schema(schema_name)
        if links is None:
            raise NotFoundError(
                f"No links found for SDM schema {schema_name}."
            )
        return EntityLinks.from_domain_models(
            subject_name=schema_name,
            domain=links,
            self_url=str(
                context.request.url_for(
                    "get_sdm_schema_links", schema_name=schema_name
                )
            ),
        )
