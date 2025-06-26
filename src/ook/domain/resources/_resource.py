from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, Field

from ..base32id import Base32Id
from ._class import ResourceClass
from ._contributor import Contributor
from ._externalref import ExternalReference
from ._relation import RelationType
from ._type import ResourceType

__all__ = [
    "ExternalRelation",
    "RelatedExternalReference",
    "RelatedResource",
    "RelatedResourceBase",
    "RelatedResourceUnion",
    "Resource",
    "ResourceRelation",
]


class Resource(BaseModel):
    """A base class for bibliographic resources."""

    id: Annotated[Base32Id, Field(description="Resource identifier.")]

    resource_class: Annotated[
        ResourceClass,
        Field(
            description=(
                "Class of the resource, used for metadata specialization."
            )
        ),
    ] = ResourceClass.generic

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

    date_resource_published: Annotated[
        datetime | None,
        Field(
            description=(
                "Date when the resource was first published, if applicable."
            )
        ),
    ] = None

    date_resource_updated: Annotated[
        datetime | None,
        Field(
            description=(
                "Date when the published resource was last updated, if "
                "applicable."
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

    contributors: Annotated[
        list[Contributor] | None,
        Field(
            description=(
                "List of contributors to the resource. Contributors of "
                "type `Creator` are considered the authors of the resource. "
                "May be None if not loaded from storage."
            ),
            default=None,
        ),
    ]

    resource_relations: Annotated[
        list[ResourceRelation] | None,
        Field(
            description=(
                "List of relations to other internal Ook resources. "
                "May be None if not loaded from storage."
            ),
            default=None,
        ),
    ]

    external_relations: Annotated[
        list[ExternalRelation] | None,
        Field(
            description=(
                "List of relations to external resources not in the Ook "
                "database. May be None if not loaded from storage."
            ),
            default=None,
        ),
    ]


class RelatedResourceBase(BaseModel):
    """Base class for related resources."""

    relation_type: Annotated[
        RelationType,
        Field(
            description="Type of the relation between resources.",
            examples=["IsCitedBy", "Cites", "IsPartOf"],
        ),
    ]


class RelatedResource(RelatedResourceBase):
    """A related resource that is in the Ook bibliographic database."""

    type: Literal["resource"] = "resource"

    resource: Annotated[Resource, Field(description="The related resource.")]


class RelatedExternalReference(RelatedResourceBase):
    """A related resource external to the Ook bibliographic database."""

    type: Literal["external"] = "external"

    external_reference: Annotated[
        ExternalReference,
        Field(
            description=(
                "External reference to the related resource. This is used "
                "when the related resource is not in the Ook bibliographic "
                "database."
            )
        ),
    ]


class ResourceRelation(BaseModel):
    """A relation to another internal Ook resource."""

    relation_type: Annotated[
        RelationType,
        Field(
            description="Type of the relation between resources.",
            examples=["IsCitedBy", "Cites", "IsPartOf"],
        ),
    ]

    resource_id: Annotated[
        Base32Id,
        Field(description="The ID of the related internal resource."),
    ]


class ExternalRelation(BaseModel):
    """A relation to an external resource."""

    relation_type: Annotated[
        RelationType,
        Field(
            description="Type of the relation between resources.",
            examples=["IsCitedBy", "Cites", "IsPartOf"],
        ),
    ]

    external_reference: Annotated[
        ExternalReference,
        Field(
            description=(
                "External reference to the related resource. This is used "
                "when the related resource is not in the Ook bibliographic "
                "database."
            )
        ),
    ]


# Union type with discriminator
RelatedResourceUnion = Annotated[
    RelatedResource | RelatedExternalReference,
    Field(discriminator="type"),
]
"""Union type for related resources, which can be either internal resources
or external references.
"""

# Rebuild the model to ensure all fields are correctly set up
Resource.model_rebuild()
