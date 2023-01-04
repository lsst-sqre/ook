"""Algolia search record types."""

from __future__ import annotations

import datetime
import uuid
from base64 import b64encode
from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl

__all__ = [
    "DocumentRecord",
    "generate_object_id",
    "generate_surrogate_key",
    "format_utc_datetime",
    "format_timestamp",
]


class DocumentRecord(BaseModel):
    """Model for an Algolia record of a document."""

    objectID: str = Field(description="The Algolia record object identifier.")

    surrogateKey: str = Field(
        description=(
            "A key that groups records from the same URL together for a given "
            "ingest so that old records can be dropped from the Algolia index."
        )
    )

    sourceUpdateTime: str = Field(
        description="An ISO 8601 date time for when the source was updated."
    )

    sourceUpdateTimestamp: int = Field(
        description=(
            "A Unix timestamp for when the source was updated. This is "
            "intended as a sortable version of `sourceUpdateTime`."
        )
    )

    sourceCreationTimestamp: Optional[int] = Field(
        None,
        description=(
            "A Unix timestamp for when the source document " "was created."
        ),
    )

    recordUpdateTime: str = Field(
        description="A ISO 8601 date time for when this record was created."
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

    baseUrl: HttpUrl = Field(
        description=(
            "The base URL of the record (whereas ``url`` may refer to an "
            "anchor link."
        )
    )

    content: str = Field(description="The full-text content of the record.")

    importance: int = Field(
        1,
        description=(
            "The importance of the record. Generally importance should be set "
            "by the header level: 1 for h1, 2 for h2, and so on."
        ),
    )

    contentCategories_lvl0: str = Field(description="Content category.")

    contentCategories_lvl1: Optional[str] = Field(
        None, description="Content sub-category (level 1)."
    )

    contentType: str = Field(description="Content type (ook classification).")

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

    authorNames: List[str] = Field(description="Names of authors.")

    h1: str = Field(description="The H1 headline (title).")

    h2: Optional[str] = Field(None, description="The h2 headline.")

    h3: Optional[str] = Field(None, description="The h3 headline.")

    h4: Optional[str] = Field(None, description="The h4 headline.")

    h5: Optional[str] = Field(None, description="The h5 headline.")

    h6: Optional[str] = Field(None, description="The h6 headline.")

    pIndex: Optional[int] = Field(
        None, description="The paragraph index corresponding to a section."
    )

    githubRepoUrl: Optional[HttpUrl] = Field(
        None, description="URL of the source repository."
    )

    class Config:
        fields = {
            "contentCategories_lvl0": "contentCategories.lvl0",
            "contentCategories_lvl1": "contentCategories.lvl1",
        }
        """Alias for fields that aren't allowable Python names."""

        allow_population_by_field_name = True
        """Enables use of Python name for constructing the record."""

        extra = "forbid"
        """Disable attributes that aren't part of the schema."""


def generate_object_id(
    *,
    url: str,
    headers: Optional[List[str]],
    paragraph_index: Optional[int] = None,
) -> str:
    """The objectID of the record.

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


def generate_surrogate_key() -> str:
    """Generate a surrogate key that applies to all records for a given
    ingest epoch of a URL.
    """
    return uuid.uuid4().hex


def format_utc_datetime(dt: datetime.datetime) -> str:
    """Format a `~datetime.datetime` in the standardized UTC `str`
    representation.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(tz=datetime.timezone.utc)
        dt = dt.replace(tzinfo=None)
    return f"{dt.isoformat()}Z"


def format_timestamp(dt: datetime.datetime) -> int:
    """Format a Unix timetsmp from a `~datetime.datetime`."""
    if dt.tzinfo is not None:
        return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())
    else:
        return int(dt.timestamp())
