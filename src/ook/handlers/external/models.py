"""Models for the external handler."""

from __future__ import annotations

import re
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, Field, root_validator
from safir.metadata import Metadata as SafirMetadata

__all__ = [
    "IndexResponse",
    "LtdIngestRequest",
]


class IndexResponse(BaseModel):
    """Metadata returned by the external root URL of the application."""

    metadata: SafirMetadata = Field(..., title="Package metadata")

    api_docs: AnyHttpUrl = Field(..., tile="API documentation URL")


class LtdIngestRequest(BaseModel):
    """Schema for `post_ingest_ltd`."""

    product_slug: str | None = None

    product_slugs: list[str] | None = Field(None)

    product_slug_pattern: str | None

    edition_slug: str = "main"

    @root_validator
    def check_slug(cls, values: dict[str, Any]) -> dict[str, Any]:
        product_slug = values.get("product_slug")
        product_slugs = values.get("product_slugs")
        product_slug_pattern = values.get("product_slug_pattern")

        if (
            product_slug is None
            and product_slugs is None
            and product_slug_pattern is None
        ):
            raise ValueError(
                "One of the ``product_slug``, ``product_slugs`` or "
                "``product_slug_pattern`` fields is required."
            )

        if product_slug_pattern is not None:
            try:
                re.compile(product_slug_pattern)
            except Exception as exc:
                raise ValueError(
                    "product_slug_pattern {self.product_slug_pattern!r} is "
                    "not a valid Python regular expression."
                ) from exc

        return values
