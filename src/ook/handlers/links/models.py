"""Models for the Links API."""

from __future__ import annotations

from typing import Self

from pydantic import AnyHttpUrl, BaseModel, Field

from ook.domain.links import SdmSchemaLink

__all__ = ["EntityLinks", "Link"]


class Link(BaseModel):
    """A documentation link."""

    url: AnyHttpUrl = Field(..., title="Documentation URL")

    kind: str = Field(..., title="Kind of the documentation")

    source_title: str = Field(..., title="Title of the documentation source")

    @classmethod
    def from_domain_model(cls, domain: SdmSchemaLink) -> Self:
        """Create a `Link` from a `SdmSchemaLink` domain model."""
        return cls(
            url=AnyHttpUrl(domain.html_url),
            kind=domain.kind,
            source_title=domain.source_title,
        )


class EntityLinks(BaseModel):
    """A collection of links to a entity."""

    name: str = Field(..., title="Name of the entity")

    links: list[Link] = Field(..., title="Links to the entity")

    self_url: AnyHttpUrl = Field(..., title="URL to this resource")

    @classmethod
    def from_domain_models(
        cls, subject_name: str, domain: list[SdmSchemaLink], self_url: str
    ) -> Self:
        """Create a `SubjectLinks` from a `SdmSchemaLink` domain model."""
        return cls(
            name=subject_name,
            links=[Link.from_domain_model(d) for d in domain],
            self_url=AnyHttpUrl(self_url),
        )
