"""Algolia search record types."""

from __future__ import annotations

import datetime
import uuid
from base64 import b64encode
from typing import List, Optional

from pydantic import BaseModel, HttpUrl

__all__ = [
    "DocumentRecord",
    "generate_object_id",
    "generate_surrogate_key",
    "format_utc_datetime",
]


class DocumentRecord(BaseModel):
    """Model for an Algolia record of a document."""

    objectID: str
    """The Algolia record object identifier."""

    surrogateKey: str
    """A key that groups records from the same URL together for a given
    ingest so that old records can be dropped from the Algolia index.
    """

    sourceUpdateTime: str
    """A timestamp for when the source was updated."""

    recordUpdateTime: str
    """A timestamp for when this record was created."""

    url: HttpUrl
    """The URL of the record."""

    baseUrl: HttpUrl
    """The base URL of the record (whereas ``url`` may refer to an anchor link.
    """

    content: str
    """The full-text content of the record."""

    importance: int = 1
    """The importance of the record.

    Generally importance should be set by the header level: 1 for h1, 2 for h2,
    and so on.
    """

    contentCategories_lvl0: str
    """Content category."""

    contentCategories_lvl1: Optional[str]
    """Content sub-category (level 1)."""

    contentType: str
    """Content type (ook classification)."""

    description: str
    """Description of the URL or short summary for the ``baseUrl``."""

    handle: str
    """Document handle."""

    number: str
    """Serial number component of the document handle."""

    series: str
    """Series component of the document handle."""

    authorNames: List[str]
    """Names of authors."""

    h1: str
    """The H1 headline (title)."""

    h2: Optional[str]
    """The h2 headline."""

    h3: Optional[str]
    """The h3 headline."""

    h4: Optional[str]
    """The h4 headline."""

    pIndex: Optional[int]
    """The paragraph index corresponding to a section."""

    githubRepoUrl: Optional[HttpUrl]
    """URL of the source repository."""

    class Config:

        fields = {
            "contentCategories_lvl0": "contentCategories.lvl0",
            "contentCategories_lvl1": "contentCategories.lvl1",
        }
        """Alias for fields that aren't allowable Python names."""

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
    return f"{dt.isoformat()}Z"
