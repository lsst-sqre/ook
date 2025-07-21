"""The /resources endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from safir.models import ErrorModel

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency
from ook.domain.base32id import Base32Id
from ook.domain.resources import Document, Resource
from ook.exceptions import NotFoundError
from ook.storage.resourcestore import ResourceLoadOptions

router = APIRouter(
    prefix=f"{config.path_prefix}/resources",
    tags=["resources"],
)


@router.get(
    "/{id}",
    summary="Get resource by ID",
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def get_resource_by_id(
    *,
    id: Annotated[
        Base32Id,
        Path(
            title="Resource ID",
            description="The Base32 identifier of the resource to retrieve.",
            examples=["1234-5678-90ab-cd2f"],
        ),
    ],
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> Resource | Document:
    """Get a resource by its ID.

    Returns the resource with the specified ID, which can be any subclass
    of Resource (e.g., Document). The response will include the appropriate
    resource_class field to indicate the specific type.
    """
    load_options = ResourceLoadOptions.all()
    async with context.session.begin():
        resource_service = context.factory.create_resource_service()
        resource = await resource_service.get_resource_by_id(
            id, load_options=load_options
        )
        if resource is None:
            raise NotFoundError(
                message=f"Resource {id!r} not found",
            )
        return resource
