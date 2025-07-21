"""Models for the ingest API."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Self

from pydantic import AnyHttpUrl, BaseModel, Field, model_validator

from ook.domain.base32id import generate_base32_id, validate_base32_id
from ook.domain.resources import (
    Contributor,
    Document,
    ExternalRelation,
    ResourceRelation,
    ResourceType,
)

__all__ = [
    "DocumentIngestRequest",
    "DocumentRequest",
    "LsstTexmfIngestRequest",
    "LtdIngestRequest",
    "SdmSchemasIngestRequest",
]


class DocumentRequest(BaseModel):
    """Schema for a single document in a document ingest request.

    This model mirrors the Document domain model but excludes fields that
    are managed by the system (id, date_created, date_updated).
    """

    title: Annotated[
        str,
        Field(
            description="Title of the document. Should be plain text.",
            examples=["My Document"],
        ),
    ]

    description: Annotated[
        str | None, Field(description="Description as plain text or Markdown.")
    ] = None

    url: Annotated[AnyHttpUrl | None, Field(description="Document URL")] = None

    doi: Annotated[
        str | None,
        Field(
            description=(
                "Digital Object Identifier (DOI) for the document, if "
                "available."
            ),
            examples=["10.1000/xyz123", "10.1109/5.771073"],
        ),
    ] = None

    date_resource_published: Annotated[
        datetime | None,
        Field(
            description=(
                "Date when the document was first published, if applicable."
            )
        ),
    ] = None

    date_resource_updated: Annotated[
        datetime | None,
        Field(
            description=(
                "Date when the document was last updated, if applicable."
            )
        ),
    ] = None

    version: Annotated[
        str | None,
        Field(
            description="Version of the document, if applicable.",
            examples=["1.0", "2.1", "3.0-beta"],
        ),
    ] = None

    type: Annotated[
        ResourceType | None,
        Field(
            description="Type of the document (DataCite vocabulary).",
        ),
    ] = None

    series: Annotated[str, Field(description="Series name of the document.")]

    handle: Annotated[
        str,
        Field(description="Project document identifier", examples=["SQR-000"]),
    ]

    generator: Annotated[
        str | None,
        Field(
            description="Document generator used to create the document",
            examples=["Documenteer 2.0.0", "Lander 2.0.0"],
        ),
    ] = None

    contributors: Annotated[
        list[Contributor] | None,
        Field(
            description=(
                "List of contributors to the document. Contributors of "
                "type `Creator` are considered the authors of the document."
            ),
            default=None,
        ),
    ]

    resource_relations: Annotated[
        list[ResourceRelation] | None,
        Field(
            description=("List of relations to other internal Ook resources."),
            default=None,
        ),
    ]

    external_relations: Annotated[
        list[ExternalRelation] | None,
        Field(
            description=(
                "List of relations to external resources not in the Ook "
                "database."
            ),
            default=None,
        ),
    ]

    def to_domain(self) -> Document:
        """Convert this request model to a Document domain model.

        Returns
        -------
        Document
            The Document domain model with a generated ID and timestamps.
        """
        now = datetime.now(tz=datetime.now().astimezone().tzinfo)

        return Document(
            id=validate_base32_id(
                generate_base32_id()
            ),  # Generate a Base32 ID and convert to integer
            date_created=now,
            date_updated=now,
            title=self.title,
            description=self.description,
            url=self.url,
            doi=self.doi,
            date_resource_published=self.date_resource_published,
            date_resource_updated=self.date_resource_updated,
            version=self.version,
            type=self.type,
            series=self.series,
            handle=self.handle,
            generator=self.generator,
            contributors=self.contributors,
            resource_relations=self.resource_relations,
            external_relations=self.external_relations,
        )


class DocumentIngestRequest(BaseModel):
    """Schema for `post_ingest_documents`."""

    documents: Annotated[
        list[DocumentRequest],
        Field(
            description="List of documents to ingest.",
            min_length=1,
        ),
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


class LsstTexmfIngestRequest(BaseModel):
    """Schema for `post_ingest_lsst_texmf`."""

    git_ref: str | None = Field(
        None,
        description=(
            "The Git reference to use for the lsst-texmf repository. If not "
            "provided, the default branch will be used."
        ),
        examples=["main"],
    )

    ingest_authordb: bool = Field(
        True,
        description=(
            "Whether to ingest the author database from the lsst-texmf "
            "repository."
        ),
    )

    ingest_glossary: bool = Field(
        True,
        description=(
            "Whether to ingest the glossary from the lsst-texmf repository."
        ),
    )

    delete_stale_records: bool = Field(
        False,
        description=(
            "Whether to delete stale records in the author store. "
            "Defaults to False."
        ),
    )
