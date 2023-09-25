"""Domain models for Algolia records."""

from __future__ import annotations

import json
import uuid
from base64 import b64encode
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Self

from pydantic import BaseModel, Field, HttpUrl

__all__ = ["DocumentRecord", "MinimalDocumentModel", "DocumentSourceType"]


class DocumentSourceType(str, Enum):
    """Types of content that can be classified by Ook."""

    LTD_TECHNOTE = "ltd_technote"
    """A technote (technote.lsst.io) that is hosted on LSST the Docs."""

    LTD_LANDER_JSONLD = "ltd_lander_jsonld"
    """A lander-based site for PDF-based content that includes a
    ``metadata.jsonld`` file and is hosted on LSST the Docs.
    """

    LTD_SPHINX_TECHNOTE = "ltd_sphinx_technote"
    """A Sphinx based technote that is hosted on LSST the Docs."""

    LTD_GENERIC = "ltd_generic"
    """A webpage that is hosted on LSST the Docs, but its precise content
    type is not known.
    """

    UNKNOWN = "unknown"
    """The content type is unclassified."""


class DocumentRecord(BaseModel):
    """Model for an Algolia record of a document."""

    object_id: str = Field(
        description="The Algolia record object identifier.",
        alias="objectID",
    )

    surrogate_key: str = Field(
        description=(
            "A key that groups records from the same URL together for a given "
            "ingest so that old records can be dropped from the Algolia index."
        ),
        alias="surrogateKey",
    )

    source_update_time: str = Field(
        description="An ISO 8601 date time for when the source was updated.",
        alias="sourceUpdateTime",
    )

    source_update_timestamp: int = Field(
        description=(
            "A Unix timestamp for when the source was updated. This is "
            "intended as a sortable version of `sourceUpdateTime`."
        ),
        alias="sourceUpdateTimestamp",
    )

    source_creation_timestamp: int | None = Field(
        None,
        description=(
            "A Unix timestamp for when the source document was created."
        ),
        alias="sourceCreationTimestamp",
    )

    record_update_time: str = Field(
        description="A ISO 8601 date time for when this record was created.",
        alias="recordUpdateTime",
    )

    # str, not HttpUrl because of
    # https://sqr-027.lsst.io/#What-is-observability?
    # Ideally we'd want to escape this properly
    url: str = Field(
        description=(
            "The URL of the record. For subsection, this URL can end with an "
            "anchor target."
        ),
        example="https://sqr-027.lsst.io/#What-is-observability?",
    )

    base_url: HttpUrl = Field(
        description=(
            "The base URL of the record (whereas ``url`` may refer to an "
            "anchor link."
        ),
        alias="baseUrl",
    )

    content: str = Field(description="The full-text content of the record.")

    importance: int = Field(
        1,
        description=(
            "The importance of the record. Generally importance should be set "
            "by the header level: 1 for h1, 2 for h2, and so on."
        ),
    )

    content_categories_lvl0: str = Field(
        description="Content category.",
        alias="contentCategories.lvl0",
    )

    content_categories_lvl1: str | None = Field(
        None,
        description="Content sub-category (level 1).",
        alias="contentCategories.lvl1",
    )

    content_type: DocumentSourceType = Field(
        description="Content type (ook classification).",
        alias="contentType",
    )

    description: str = Field(
        description=(
            "Description of the URL or short summary for the ``baseUrl``."
        )
    )

    handle: str = Field(description="Document handle.")

    number: int = Field(
        description=(
            "Serial number component of the document handle (``handle``)."
        )
    )

    series: str = Field(
        description="Series component of the document handle (``handle``)."
    )

    author_names: list[str] = Field(
        description="Names of authors.",
        alias="authorNames",
    )

    h1: str = Field(description="The H1 headline (title).")

    h2: str | None = Field(None, description="The h2 headline.")

    h3: str | None = Field(None, description="The h3 headline.")

    h4: str | None = Field(None, description="The h4 headline.")

    h5: str | None = Field(None, description="The h5 headline.")

    h6: str | None = Field(None, description="The h6 headline.")

    p_index: int | None = Field(
        None,
        description="The paragraph index corresponding to a section.",
        alias="pIndex",
    )

    github_repo_url: HttpUrl | None = Field(
        None,
        description="URL of the source repository.",
        alias="githubRepoURL",
    )

    class Config:
        allow_population_by_field_name = True
        """Enables use of Python name for constructing the record."""

        extra = "forbid"
        """Disable attributes that aren't part of the schema."""

    @staticmethod
    def generate_object_id(
        *,
        url: str,
        headers: list[str] | None = None,
        paragraph_index: int | None = None,
    ) -> str:
        """Create the objectID of a document record.

        This is computed based on the URL, section heading hierarchy, and
        index paragraph.
        """
        # start with the URL component.
        components = [b64encode(url.lower().encode("utf-8")).decode("utf-8")]

        # Add heading components
        if headers:
            components.append(
                b64encode(" ".join(headers).encode("utf-8")).decode("utf-8")
            )

        # Add paragraph index
        if paragraph_index:
            components.append(str(paragraph_index))

        return "-".join(components)

    def export_for_algolia(self) -> dict[str, Any]:
        """Export into a dict that can be uploaded to Algolia."""
        return self.dict(by_alias=True, exclude_none=True)

    @property
    def headers(self) -> list[str | None]:
        """The headers of the document."""
        return [self.h1, self.h2, self.h3, self.h4, self.h5, self.h6]


def generate_surrogate_key() -> str:
    """Generate a surrogate key that applies to all records for a given
    ingest epoch of a URL.
    """
    return uuid.uuid4().hex


def format_utc_datetime(dt: datetime) -> str:
    """Format a `~datetime.datetime` in the standardized UTC `str`
    representation.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(tz=UTC)
        dt = dt.replace(tzinfo=None)
    return f"{dt.isoformat()}Z"


def format_timestamp(dt: datetime) -> int:
    """Format a Unix timetsmp from a `~datetime.datetime`."""
    if dt.tzinfo is not None:
        return int(dt.replace(tzinfo=UTC).timestamp())
    else:
        return int(dt.timestamp())


class MinimalDocumentModel(BaseModel):
    """A model for a manually-added document record."""

    title: str = Field(description="Document's title")

    handle: str = Field(description="Document handle.")

    url: HttpUrl = Field(description="The document's URL.")

    author_names: list[str] = Field(
        default_factory=list,
        description="Author names",
        alias="authorNames",
    )

    description: str = Field(description="Description of the document.")

    github_repo_url: HttpUrl | None = Field(
        None,
        description="URL of the source repository.",
        alias="githubRepoURL",
    )

    @classmethod
    def from_json(cls, data: str) -> Self:
        """Create a MinimalDocumentModel from JSON."""
        return cls.parse_obj(json.loads(data))

    def make_algolia_record(self) -> DocumentRecord:
        object_id = DocumentRecord.generate_object_id(
            url=str(self.url), headers=[self.title]
        )
        surrogate_key = generate_surrogate_key()
        now = datetime.now(tz=UTC)
        series, _number = self.handle.split("-")

        return DocumentRecord(
            object_id=object_id,
            base_url=self.url,
            url=self.url,
            surrogate_key=surrogate_key,
            source_update_time=format_utc_datetime(now),
            source_update_timestamp=format_timestamp(now),
            source_creation_timestamp=None,
            record_update_time=format_utc_datetime(now),
            content_categories_lvl0="Documents",
            content_categories_lvl1=f"Documents > {series.upper()}",
            content_type=DocumentSourceType.UNKNOWN,
            description=self.description,
            content=self.description,
            handle=self.handle,
            number=int(_number),
            series=series,
            author_names=self.author_names,
            p_index=None,
            h1=self.title,
            github_repo_url=self.github_repo_url,
        )
