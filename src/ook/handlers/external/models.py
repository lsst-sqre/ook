"""Models for the external handler."""

from __future__ import annotations

import re
from typing import Self

from pydantic import AnyHttpUrl, BaseModel, Field, model_validator
from safir.metadata import Metadata as SafirMetadata

from ook.domain.links import SdmSchemaLink

__all__ = [
    "IndexResponse",
    "LtdIngestRequest",
]


class IndexResponse(BaseModel):
    """Metadata returned by the external root URL of the application."""

    metadata: SafirMetadata = Field(..., title="Package metadata")

    api_docs: AnyHttpUrl = Field(..., title="API documentation URL")


class LtdIngestRequest(BaseModel):
    """Schema for `post_ingest_ltd`."""

    product_slug: str | None = None

    product_slugs: list[str] | None = Field(None)

    product_slug_pattern: str | None = None

    edition_slug: str = "main"

    @model_validator(mode="after")
    def check_slug(self) -> Self:
        if (
            self.product_slug is None
            and self.product_slugs is None
            and self.product_slug_pattern is None
        ):
            raise ValueError(
                "One of the ``product_slug``, ``product_slugs`` or "
                "``product_slug_pattern`` fields is required."
            )

        if self.product_slug_pattern is not None:
            try:
                re.compile(self.product_slug_pattern)
            except Exception as exc:
                raise ValueError(
                    "product_slug_pattern {self.product_slug_pattern!r} is "
                    "not a valid Python regular expression."
                ) from exc

        return self


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
