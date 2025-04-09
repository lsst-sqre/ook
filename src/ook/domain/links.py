"""Domain models for documentation deep links."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, RootModel

__all__ = [
    "Link",
    "LinksCollection",
    "SdmColumnLink",
    "SdmColumnLinksCollection",
    "SdmLinksCollection",
    "SdmSchemaLink",
    "SdmSchemaLinksCollection",
    "SdmTableLink",
    "SdmTableLinksCollection",
]


class Link(BaseModel):
    """A link to documentation.

    This is a Pydantic model to facilitate the parsing of SQLAlchemy queries
    directly into the domain.
    """

    html_url: str = Field(description="The URL to the documentation page.")

    title: str = Field(description="The title of the documentation.")

    type: str = Field(description="The type of documentation.")

    collection_title: str | None = Field(
        None,
        description=(
            "The title of the collection of documentation this link refers to."
        ),
    )


class SdmSchemaLink(Link):
    """A link to an SDM schema's documentation."""

    schema_name: str = Field(description="The name of the schema.")


class SdmTableLink(Link):
    """A link to an SDM table's documentation."""

    schema_name: str = Field(description="The name of the schema.")

    table_name: str = Field(description="The name of the table.")


class SdmColumnLink(Link):
    """A link to an SDM column's documentation."""

    schema_name: str = Field(description="The name of the schema.")

    table_name: str = Field(description="The name of the table.")

    column_name: str = Field(description="The name of the column.")


class LinksCollection[T: Link](BaseModel):
    """A collection of links to documentation of a specific entity.

    This is a Pydantic model to facilitate the parsing of SQLAlchemy queries
    directly into the domain.
    """

    links: list[T] = Field(description="The documentation links.")


class SdmSchemaLinksCollection(LinksCollection[SdmSchemaLink]):
    """A collection of links to an SDM schema."""

    domain: Literal["sdm"] = Field(
        description="The links domain of the entity."
    )

    entity_type: Literal["schema"] = Field(
        description="The type of the entity in the domain."
    )

    schema_name: str = Field(description="The name of the schema.")


class SdmTableLinksCollection(LinksCollection[SdmTableLink]):
    """A collection of links to an SDM table."""

    domain: Literal["sdm"] = Field(
        description="The links domain of the entity."
    )

    entity_type: Literal["table"] = Field(
        description="The type of the entity in the domain."
    )

    schema_name: str = Field(description="The name of the schema.")

    table_name: str = Field(description="The name of the table.")


class SdmColumnLinksCollection(LinksCollection[SdmColumnLink]):
    """A collection of links to SDM columns."""

    domain: Literal["sdm"] = Field(
        description="The links domain of the entity."
    )

    entity_type: Literal["column"] = Field(
        description="The type of the entity in the domain."
    )

    schema_name: str = Field(description="The name of the schema.")

    table_name: str = Field(description="The name of the table.")

    column_name: str = Field(description="The name of the column.")


SdmLinksCollection = RootModel[
    Annotated[
        SdmSchemaLinksCollection
        | SdmTableLinksCollection
        | SdmColumnLinksCollection,
        Field(discriminator="entity_type"),
    ]
]
"""A generic collection of SDM links for any SDM entity type.

Use this root model to parse SQLAlchemy query results when the domain type
can be any of the three SDM entity types: schema, table, or column.

Access the underlying collection using the ``root`` attribute.
"""
