"""Models for the Links API."""

from __future__ import annotations

from typing import Self

from pydantic import AnyHttpUrl, BaseModel, Field

from ook.domain.links import Link as DomainLink

__all__ = ["Link"]


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
