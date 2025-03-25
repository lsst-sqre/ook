"""Models for the Links API."""

from __future__ import annotations

from typing import Self

from fastapi import Request
from pydantic import AnyHttpUrl, BaseModel, Field

from ook.domain.links import Link as DomainLink
from ook.domain.links import SdmColumnLinksCollection

__all__ = ["Link", "SdmColumnLinks"]


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


class SdmColumnLinks(BaseModel):
    """Documentation links for an SDM column."""

    schema_name: str = Field(..., title="Name of the schema")
    table_name: str = Field(..., title="Name of the table")
    column_name: str = Field(..., title="Name of the column")
    links: list[Link] = Field(..., title="Documentation links")
    self_url: str = Field(..., title="Link to this resource")

    @classmethod
    def from_domain(
        cls,
        *,
        domain_collection: list[SdmColumnLinksCollection],
        request: Request,
    ) -> list[Self]:
        """Create a `SdmColumnLinks` from a `SdmColumnLinksCollection`."""
        return [
            cls(
                schema_name=domain_column_links.schema_name,
                table_name=domain_column_links.table_name,
                column_name=domain_column_links.column_name,
                links=[
                    Link.from_domain_link(link)
                    for link in domain_column_links.links
                ],
                self_url=str(
                    request.url_for(
                        "get_sdm_schema_column_links",
                        schema_name=domain_column_links.schema_name,
                        table_name=domain_column_links.table_name,
                        column_name=domain_column_links.column_name,
                    )
                ),
            )
            for domain_column_links in domain_collection
        ]
