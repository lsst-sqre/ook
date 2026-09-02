"""Models for the Links API."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar, Literal, Self

from fastapi import FastAPI, Request
from pydantic import AnyHttpUrl, BaseModel, Field

from ook.domain.intersphinxentities import IntersphinxEntityLinks
from ook.domain.links import Link as DomainLink
from ook.domain.links import (
    SdmColumnLinksCollection,
    SdmLinksCollection,
    SdmSchemaLinksCollection,
    SdmTableLinksCollection,
)

__all__ = [
    "LINK_DOMAIN_TYPES",
    "Link",
    "LinkDomainInfo",
    "LinkDomainSummary",
    "LinkDomainTemplates",
    "LinkedEntityInfo",
    "PythonDomainInfo",
    "PythonObjectLinkedEntityInfo",
    "PythonObjectLinks",
    "SdmColumnLinkedEntityInfo",
    "SdmDomainInfo",
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


class LinkDomainTemplates(BaseModel):
    """The URI templates one link domain publishes.

    Every link domain answers the same two questions -- how to address one
    of its entities, and how to page through a collection of them -- so the
    shape is shared and each domain fills in its own templates. A client
    that has read one domain's templates can therefore read any of them,
    whether it read them from the domain itself or from the domains index.
    """

    entities: dict[str, str] = Field(
        ...,
        title="Entities in the domain",
        description=(
            "URI templates addressing one entity, keyed by the kind of "
            "entity the template addresses."
        ),
    )

    collections: dict[str, str] = Field(
        ...,
        title="Collections in the domain",
        description=(
            "URI templates addressing a collection of entities, keyed by "
            "the kind of collection the template addresses."
        ),
    )


class LinkDomainInfo(LinkDomainTemplates):
    """What one link domain's own info endpoint answers.

    Subclasses name themselves and build their own templates; this class is
    the shared behavior rather than a domain in its own right.
    """

    domain_name: ClassVar[str]
    """This domain's name in the ``/links/domains/`` namespace."""

    info_route_name: ClassVar[str]
    """Name of the route serving this domain's own info endpoint."""

    @classmethod
    def create(cls, request: Request) -> Self:
        """Build this domain's URI templates against the request's base URL."""
        raise NotImplementedError

    @classmethod
    def create_summary(cls, request: Request) -> LinkDomainSummary:
        """Summarize this domain for the cross-domain index.

        A domain describes itself the same way wherever it is asked, so the
        index reuses each domain's own ``create`` rather than restating its
        templates in a second place that could drift.
        """
        info = cls.create(request)
        return LinkDomainSummary(
            name=cls.domain_name,
            self_url=str(request.url_for(cls.info_route_name)),
            entities=info.entities,
            collections=info.collections,
        )


class SdmDomainInfo(LinkDomainInfo):
    """Links for the SDM domain APIs."""

    domain_name: ClassVar[str] = "sdm"

    info_route_name: ClassVar[str] = "get_sdm_domain_info"

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


class PythonDomainInfo(LinkDomainInfo):
    """Links for the Python domain APIs."""

    domain_name: ClassVar[str] = "python"

    info_route_name: ClassVar[str] = "get_python_domain_info"

    @classmethod
    def create(cls, request: Request) -> Self:
        """Create a `PythonDomainInfo` object."""
        base_url = str(request.base_url).removesuffix("/")
        app = request.app
        return cls(
            entities={
                "object": base_url
                + _path_template(app, "get_python_object_links", "name"),
            },
            collections={
                "objects": base_url
                + _path_template(app, "get_python_objects"),
                "children": base_url
                + _path_template(app, "get_python_object_children", "name"),
            },
        )


LINK_DOMAIN_TYPES: tuple[type[LinkDomainInfo], ...] = (
    SdmDomainInfo,
    PythonDomainInfo,
)
"""Every link domain the API publishes, in the order the index lists them.

The index is generated from this tuple, so a new domain becomes discoverable
by being registered here rather than by remembering to extend a hand-written
listing.
"""


class LinkDomainSummary(LinkDomainTemplates):
    """One link domain's entry in the cross-domain index.

    Carries the domain's own URI templates, so a client can address entities
    in any domain straight from the index, plus the two things that only
    mean something alongside the domain's siblings: which domain this is,
    and where its own info endpoint lives.
    """

    name: str = Field(
        ...,
        title="Name of the domain",
        description=(
            "The domain's name in the ``/links/domains/`` namespace, which "
            "is also what entities in it report as their ``domain``."
        ),
        examples=["sdm"],
    )

    self_url: str = Field(
        ..., title="API URL to this domain's own info endpoint"
    )

    @classmethod
    def create_all(cls, request: Request) -> list[LinkDomainSummary]:
        """Summarize every registered link domain, in registration order."""
        return [
            domain_type.create_summary(request)
            for domain_type in LINK_DOMAIN_TYPES
        ]


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


class PythonObjectLinkedEntityInfo(LinkedEntityInfo):
    """Information about a Python object links entity."""

    domain: Literal["python"] = "python"

    domain_type: Literal["object"] = "object"

    name: str = Field(
        ...,
        title="Fully qualified name of the object",
        description=(
            "The name a Sphinx cross-reference targets, which is the "
            "object's identity in this domain."
        ),
    )

    @classmethod
    def from_domain(
        cls, *, domain: IntersphinxEntityLinks, request: Request
    ) -> Self:
        """Create a `PythonObjectLinkedEntityInfo` from a stored entity."""
        return cls(
            name=domain.name,
            self_url=str(
                request.url_for("get_python_object_links", name=domain.name)
            ),
        )


class PythonObjectLinks(BaseModel):
    """Documentation links for one Python object."""

    entity: PythonObjectLinkedEntityInfo = Field(
        ..., title="Identity about the linked entity"
    )

    links: list[Link] = Field(
        ...,
        title="Documentation links",
        description=(
            "Empty for an object Ook knows but no source currently gives a "
            "page -- a package held in place by the documented objects "
            "beneath it."
        ),
    )

    @classmethod
    def from_domain(
        cls,
        *,
        domain_collection: Sequence[IntersphinxEntityLinks],
        request: Request,
    ) -> list[Self]:
        """Create a `PythonObjectLinks` list from stored entities."""
        return [
            cls(
                entity=PythonObjectLinkedEntityInfo.from_domain(
                    domain=domain, request=request
                ),
                links=[Link.from_domain_link(link) for link in domain.links],
            )
            for domain in domain_collection
        ]
