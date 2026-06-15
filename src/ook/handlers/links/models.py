"""Models for the Links API."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Self

from fastapi import FastAPI, Request
from pydantic import AnyHttpUrl, BaseModel, Field

from ook.domain.links import Link as DomainLink
from ook.domain.links import (
    SdmColumnLinksCollection,
    SdmLinksCollection,
    SdmSchemaLinksCollection,
    SdmTableLinksCollection,
)

__all__ = [
    "Link",
    "LinkedEntityInfo",
    "SdmColumnLinkedEntityInfo",
    "SdmLinks",
    "SdmSchemaLinkedEntityInfo",
    "SdmTableLinkedEntityInfo",
]


def _path_template(app: FastAPI, route_name: str, *param_names: str) -> str:
    """Return the URI path template for a named route.

    Path parameters are rendered as ``{name}`` placeholders so the result is a
    URI template rather than a concrete URL.
    """
    placeholders = {p: f"{{{p}}}" for p in param_names}
    return str(app.url_path_for(route_name, **placeholders))


class SdmDomainInfo(BaseModel):
    """Links for the SDM domain APIs."""

    entities: dict[str, str] = Field(
        ...,
        title="Entities in the SDM domain",
    )

    collections: dict[str, str] = Field(
        ..., title="Collections in the SDM domain"
    )

    @classmethod
    def create(cls, request: Request) -> Self:
        """Create a `SdmDomainInfo` object."""
        base_url = str(request.base_url).removesuffix("/")
        app = request.app
        return cls(
            entities={
                "schema": base_url
                + _path_template(app, "get_sdm_schema_links", "schema_name"),
                "table": base_url
                + _path_template(
                    app,
                    "get_sdm_schema_table_links",
                    "schema_name",
                    "table_name",
                ),
                "column": base_url
                + _path_template(
                    app,
                    "get_sdm_schema_column_links",
                    "schema_name",
                    "table_name",
                    "column_name",
                ),
            },
            collections={
                "schemas": base_url + _path_template(app, "get_sdm_links"),
                "tables": base_url
                + _path_template(
                    app, "get_sdm_links_scoped_to_schema", "schema_name"
                ),
                "columns": base_url
                + _path_template(
                    app,
                    "get_sdm_schema_column_links_for_table",
                    "schema_name",
                    "table_name",
                ),
            },
        )


class Link(BaseModel):
    """A documentation link."""

    url: AnyHttpUrl = Field(..., title="Documentation URL")

    title: str = Field(
        ...,
        title="Title of the resource",
        description=(
            "The title of the page or section that this link references."
        ),
    )

    type: str = Field(..., title="Type of documentation")

    collection_title: str | None = Field(
        None,
        title="Title of the documentation collection",
        description=(
            "For a link into a user guide, this would be the title of "
            "the user guide itself."
        ),
    )

    @classmethod
    def from_domain_link(cls, link: DomainLink) -> Self:
        """Create a `Link` from a `SdmSchemaLink` domain model."""
        return cls(
            url=AnyHttpUrl(link.html_url),
            title=link.title,
            type=link.type,
            collection_title=link.collection_title,
        )


class LinkedEntityInfo(BaseModel):
    """Information about an entity."""

    domain: str = Field(..., title="Links domain of the entity")

    domain_type: str = Field(..., title="Type of the entity in the domain")

    self_url: str = Field(..., title="API URL to this resource")


class SdmSchemaLinkedEntityInfo(LinkedEntityInfo):
    """Information about an SDM schema links entity."""

    domain: Literal["sdm"] = "sdm"

    domain_type: Literal["schema"] = "schema"

    schema_name: str = Field(..., title="Name of the schema")

    @classmethod
    def from_domain(
        cls, *, domain: SdmSchemaLinksCollection, request: Request
    ) -> Self:
        """Create a `SdmSchemaLinkedEntityInfo` from a
        `SdmSchemaLinksCollection`.
        """
        return cls(
            schema_name=domain.schema_name,
            self_url=str(
                request.url_for(
                    "get_sdm_schema_links",
                    schema_name=domain.schema_name,
                )
            ),
        )


class SdmTableLinkedEntityInfo(LinkedEntityInfo):
    """Information about an SDM table links entity."""

    domain: Literal["sdm"] = "sdm"

    domain_type: Literal["table"] = "table"

    schema_name: str = Field(..., title="Name of the schema")

    table_name: str = Field(..., title="Name of the table")

    @classmethod
    def from_domain(
        cls, *, domain: SdmTableLinksCollection, request: Request
    ) -> Self:
        """Create a `SdmTableLinkedEntityInfo` from a
        `SdmTableLinksCollection`.
        """
        return cls(
            schema_name=domain.schema_name,
            table_name=domain.table_name,
            self_url=str(
                request.url_for(
                    "get_sdm_schema_table_links",
                    schema_name=domain.schema_name,
                    table_name=domain.table_name,
                )
            ),
        )


class SdmColumnLinkedEntityInfo(LinkedEntityInfo):
    """Information about an SDM column links entity."""

    domain: Literal["sdm"] = "sdm"

    domain_type: Literal["column"] = "column"

    schema_name: str = Field(..., title="Name of the schema")

    table_name: str = Field(..., title="Name of the table")

    column_name: str = Field(..., title="Name of the column")

    @classmethod
    def from_domain(
        cls, *, domain: SdmColumnLinksCollection, request: Request
    ) -> Self:
        """Create a `SdmColumnLinkedEntityInfo` from a
        `SdmColumnLinksCollection`.
        """
        return cls(
            schema_name=domain.schema_name,
            table_name=domain.table_name,
            column_name=domain.column_name,
            self_url=str(
                request.url_for(
                    "get_sdm_schema_column_links",
                    schema_name=domain.schema_name,
                    table_name=domain.table_name,
                    column_name=domain.column_name,
                )
            ),
        )


sdm_entity_types = (
    SdmSchemaLinkedEntityInfo
    | SdmTableLinkedEntityInfo
    | SdmColumnLinkedEntityInfo
)


class SdmLinks(BaseModel):
    """Documentation links for an SDM column."""

    entity: sdm_entity_types = Field(
        ..., title="Identity about the linked entity"
    )

    links: list[Link] = Field(..., title="Documentation links")

    @classmethod
    def from_domain(
        cls,
        *,
        domain_collection: Sequence[
            SdmColumnLinksCollection
            | SdmTableLinksCollection
            | SdmSchemaLinksCollection
        ],
        request: Request,
    ) -> list[Self]:
        """Create a `SdmColumnLinks` a sequence of SDM link collections.

        This method can be used for single-type collections. For mult-type
        collections use `from_sdm_links_collection`.
        """
        return [
            cls(
                entity=cls._create_entity_info(domain, request),
                links=[Link.from_domain_link(link) for link in domain.links],
            )
            for domain in domain_collection
        ]

    @classmethod
    def from_sdm_links_collection(
        cls,
        *,
        sdm_links_collections: Sequence[SdmLinksCollection],
        request: Request,
    ) -> list[Self]:
        """Create a `SdmLinks` from an `SdmLinksCollection` sequence.

        The SdmLinksCollection can be any of the three types:

        - `SdmSchemaLinksCollection`
        - `SdmTableLinksCollection`
        - `SdmColumnLinksCollection`

        This method will create a list of SdmLinks objects for each
        SdmLinksCollection in the sequence.
        """
        return [
            cls(
                entity=cls._create_entity_info(
                    sdm_links_collection.root, request
                ),
                links=[
                    Link.from_domain_link(link)
                    for link in sdm_links_collection.root.links
                ],
            )
            for sdm_links_collection in sdm_links_collections
        ]

    @classmethod
    def _create_entity_info(
        cls,
        domain: SdmSchemaLinksCollection
        | SdmTableLinksCollection
        | SdmColumnLinksCollection,
        request: Request,
    ) -> sdm_entity_types:
        """Create the appropriate entity info for the domain."""
        match domain:
            case SdmSchemaLinksCollection():
                return SdmSchemaLinkedEntityInfo.from_domain(
                    domain=domain, request=request
                )
            case SdmTableLinksCollection():
                return SdmTableLinkedEntityInfo.from_domain(
                    domain=domain, request=request
                )
            case SdmColumnLinksCollection():
                return SdmColumnLinkedEntityInfo.from_domain(
                    domain=domain, request=request
                )
            case _:
                raise TypeError(f"Unknown domain type: {type(domain)}")
