"""Models for the ingest API."""

from __future__ import annotations

import re
from typing import Self

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "LtdIngestRequest",
    "SdmSchemasIngestRequest",
]


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


class SdmSchemasIngestRequest(BaseModel):
    """Schema for `post_ingest_sdm_schemas`."""

    github_owner: str = Field(
        "lsst",
        description=(
            "The GitHub owner of the SDM schemas repository to ingest."
        ),
        examples=["lsst"],
    )

    github_repo: str = Field(
        "sdm_schemas",
        description="The GitHub repository of the SDM schemas to ingest.",
        examples=["sdm_schemas"],
    )

    github_release_tag: str | None = Field(
        None,
        description=(
            "The GitHub release tag to ingest. If not provided, "
            "the latest release will be ingested."
        ),
        examples=["w.2025.10"],
    )
