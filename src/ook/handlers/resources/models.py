"""API models for resources."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Self

from fastapi import Request
from pydantic import AnyHttpUrl, BaseModel, Field

from ook.domain.base32id import Base32Id, serialize_ook_base32_id
from ook.domain.resources import (
    ContributorRole,
    ExternalReference,
    RelatedExternalReference,
    RelatedResourceSummary,
    RelationType,
    ResourceClass,
    ResourceSummary,
    ResourceType,
)
from ook.domain.resources import Document as DocumentDomain
from ook.domain.resources import Resource as ResourceDomain

from ..authors.models import Author


class ResourceSummaryAPI(BaseModel):
    """A summary representation of a resource for API responses."""

    id: Annotated[Base32Id, Field(description="Resource identifier.")]

    title: Annotated[
        str,
        Field(
            description="Title of the resource. Should be plain text.",
            examples=["My Resource"],
        ),
    ]

    description: Annotated[
        str | None, Field(description="Description as plain text or Markdown.")
    ] = None

    url: Annotated[AnyHttpUrl | None, Field(description="Resource URL")] = None

    doi: Annotated[
        str | None,
        Field(
            description=(
                "Digital Object Identifier (DOI) for the resource, if "
                "available."
            ),
            examples=["10.1000/xyz123", "10.1109/5.771073"],
        ),
    ] = None

    self_url: Annotated[
        str,
        Field(description=("URL to access this resource record in the API")),
    ]

    @classmethod
    def from_domain(
        cls, resource: ResourceSummary, request: Request
    ) -> ResourceSummaryAPI:
        """Create a ResourceSummaryAPI from a domain ResourceSummary."""
        return cls(
            id=resource.id,
            title=resource.title,
            description=resource.description,
            url=resource.url,
            doi=resource.doi,
            self_url=str(
                request.url_for(
                    "get_resource_by_id",
                    id=serialize_ook_base32_id(resource.id),
                )
            ),
        )


class RelatedResourceAPI(BaseModel):
    """A related resource in the API response."""

    relation_type: Annotated[
        RelationType,
        Field(
            description="Type of the relation between resources.",
            examples=["IsCitedBy", "Cites", "IsPartOf"],
        ),
    ]

    resource_type: Annotated[
        Literal["resource", "external"],
        Field(
            description="Type of the related resource, either 'resource' for "
            "internal resources or 'external' for external references.",
            examples=["resource", "external"],
        ),
    ]

    resource: Annotated[
        ResourceSummaryAPI | ExternalReference,
        Field(
            description=(
                "The related resource. Internal resources are provided as "
                "summaries, while external resources include full metadata."
            )
        ),
    ]

    @classmethod
    def from_domain(
        cls,
        related: RelatedResourceSummary | RelatedExternalReference,
        request: Request,
    ) -> RelatedResourceAPI:
        """Create a RelatedResourceAPI from domain models."""
        if isinstance(related, RelatedResourceSummary):
            return cls(
                relation_type=related.relation_type,
                resource_type="resource",
                resource=ResourceSummaryAPI.from_domain(
                    related.resource, request
                ),
            )
        else:  # RelatedExternalReference
            return cls(
                relation_type=related.relation_type,
                resource_type="external",
                resource=related.external_reference,
            )


class ResourceMetadata(BaseModel):
    """Metadata about a resource record."""

    date_created: Annotated[
        datetime,
        Field(
            description="Date when the resource database record was created.",
        ),
    ]

    date_updated: Annotated[
        datetime,
        Field(
            description=(
                "Date when the resource database record was last modified."
            )
        ),
    ]


class GenericResource[ResourceDomainT: ResourceDomain](BaseModel):
    """A resource."""

    id: Annotated[Base32Id, Field(description="Resource identifier.")]

    resource_class: Annotated[
        ResourceClass,
        Field(
            description=(
                "Class of the resource, used for metadata specialization."
            )
        ),
    ] = ResourceClass.generic

    title: Annotated[
        str,
        Field(
            description="Title of the resource. Should be plain text.",
            examples=["My Resource"],
        ),
    ]

    description: Annotated[
        str | None, Field(description="Description as plain text or Markdown.")
    ] = None

    url: Annotated[AnyHttpUrl | None, Field(description="Resource URL")] = None

    doi: Annotated[
        str | None,
        Field(
            description=(
                "Digital Object Identifier (DOI) for the resource, if "
                "available."
            ),
            examples=["10.1000/xyz123", "10.1109/5.771073"],
        ),
    ] = None

    date_published: Annotated[
        datetime | None,
        Field(
            description=(
                "Date when the resource was first published, if applicable."
            )
        ),
    ] = None

    date_updated: Annotated[
        datetime | None,
        Field(
            description=(
                "Date when the resource was last updated, if applicable."
            )
        ),
    ] = None

    version: Annotated[
        str | None,
        Field(
            description="Version of the resource, if applicable.",
            examples=["1.0", "2.1", "3.0-beta"],
        ),
    ] = None

    type: Annotated[
        ResourceType | None,
        Field(
            description="Type of the resource (DataCite vocabulary).",
        ),
    ] = None

    self_url: Annotated[
        str,
        Field(description=("URL to access this resource record in the API")),
    ]

    metadata: Annotated[
        ResourceMetadata, Field(description="Metadata about the resource.")
    ]

    creators: Annotated[list[Author], Field(description="Resource creators.")]

    contributors: Annotated[
        dict[ContributorRole, list[Author]],
        Field(
            description="Contributors to the resource, grouped by role.",
            default_factory=dict,
        ),
    ]

    related: Annotated[
        list[RelatedResourceAPI],
        Field(
            description=(
                "Related resources with their relation types. Internal "
                "resources are provided as summaries to avoid recursive "
                "loading, while external resources include full metadata."
            ),
            default_factory=list,
        ),
    ]

    @classmethod
    def from_domain(cls, resource: ResourceDomainT, request: Request) -> Self:
        """Create a `GenericResource` from a `ResourceDomain`."""
        fields = cls.create_fields(resource, request)
        return cls.model_validate(fields, from_attributes=True)

    @classmethod
    def create_fields(
        cls, resource: ResourceDomainT, request: Request
    ) -> dict[str, Any]:
        """Create a fields domain Resource."""
        creators = [
            Author.from_domain(creator.author)
            for creator in resource.contributors
            if creator.role == ContributorRole.creator
        ]

        roles = list(
            {
                role
                for role in ContributorRole
                if role != ContributorRole.creator
            }
        )

        contributors = {
            role: [
                Author.from_domain(contributor.author)
                for contributor in resource.contributors
                if contributor.role == role
            ]
            for role in roles
        }

        # Convert related resources to API format
        related = [
            RelatedResourceAPI.from_domain(rel, request)
            for rel in resource.related_resources
        ]

        return {
            "id": resource.id,
            "resource_class": resource.resource_class,
            "title": resource.title,
            "description": resource.description,
            "url": resource.url,
            "doi": resource.doi,
            "date_published": resource.date_resource_published,
            "date_updated": resource.date_resource_updated,
            "version": resource.version,
            "type": resource.type,
            "self_url": str(
                request.url_for(
                    "get_resource_by_id",
                    id=serialize_ook_base32_id(
                        resource.id,
                    ),
                )
            ),
            "metadata": ResourceMetadata(
                date_created=resource.date_created,
                date_updated=resource.date_updated,
            ),
            "creators": creators,
            "contributors": contributors,
            "related": related,
        }


class DocumentResource(GenericResource[DocumentDomain]):
    """A document resource."""

    resource_class: Annotated[
        ResourceClass, Field(default=ResourceClass.document)
    ] = ResourceClass.document

    handle: Annotated[str, Field(description="Document handle.")]

    series: Annotated[str, Field(description="Series name of the document.")]

    generator: Annotated[
        str | None,
        Field(
            description="Document generator used to create the document",
            examples=["Documenteer 2.0.0", "Lander 2.0.0"],
        ),
    ] = None

    number: Annotated[
        int,
        Field(
            description=(
                "Numeric component of handle for sorting within series"
            ),
            examples=[50, 123, 1],
        ),
    ]

    @classmethod
    def from_domain(cls, resource: DocumentDomain, request: Request) -> Self:
        """Create a `DocumentResource` from a `ResourceDomain`."""
        fields = cls.create_fields(resource, request)
        fields["handle"] = resource.handle
        fields["series"] = resource.series
        fields["generator"] = resource.generator
        fields["number"] = resource.number

        return cls.model_validate(fields, from_attributes=True)


def create_resource_from_domain(
    resource: ResourceDomain | DocumentDomain, request: Request
) -> GenericResource | DocumentResource:
    """Create a resource model from a domain resource."""
    if isinstance(resource, DocumentDomain):
        return DocumentResource.from_domain(resource, request)
    return GenericResource.from_domain(resource, request)
