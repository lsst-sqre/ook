"""API models for the intersphinx documentation source registry."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Self

from fastapi import Request
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, HttpUrl

from ook.domain.base32id import Base32Id, serialize_ook_base32_id
from ook.domain.intersphinxsources import (
    IntersphinxSource as IntersphinxSourceDomain,
)
from ook.domain.intersphinxsources import SourceIngestStatus

__all__ = [
    "IntersphinxSource",
    "IntersphinxSourceRequest",
    "IntersphinxSourceUpdateRequest",
    "IntersphinxSourceWriteRequest",
]

MAX_INVENTORY_URL_LENGTH = 2048
"""Longest inventory URL the registry accepts.

Well under pydantic's own 2083-character `~pydantic.HttpUrl` cap, and
stated explicitly so the bound is a published contract rather than a
library default that could move.
"""

MAX_TITLE_LENGTH = 255
"""Longest site title the registry accepts.

The title is displayed to readers as a link's ``collection_title``, so it
is a label rather than prose.
"""


def _require_https(url: HttpUrl) -> HttpUrl:
    """Reject an inventory URL that does not use ``https``.

    The ingest path fetches every registered inventory through the
    intersphinx cache service, whose SSRF guard refuses anything but
    ``https``. Registering an ``http`` URL would therefore create a row
    that can only ever fail its ingest, so the scheme is checked here --
    where the operator is present to fix it -- rather than left to surface
    as a stamped failure on the next scheduled run.
    """
    if url.scheme != "https":
        raise ValueError("An inventory URL must use the https scheme.")
    return url


InventoryUrl = Annotated[
    HttpUrl,
    AfterValidator(_require_https),
    Field(
        description=(
            "The full URL of the site's ``objects.inv`` inventory. This is"
            " the source's identity, so registering the same inventory"
            " twice is a conflict rather than a second row."
        ),
        max_length=MAX_INVENTORY_URL_LENGTH,
        examples=["https://pipelines.lsst.io/objects.inv"],
    ),
]
"""An ``https`` inventory URL, bounded in length."""

SiteTitle = Annotated[
    str,
    Field(
        description=(
            "The human title of the documentation site. It surfaces as the"
            " ``collection_title`` of every link ingested from this"
            " source, so it is what a reader sees naming the site a link"
            " goes to."
        ),
        min_length=1,
        max_length=MAX_TITLE_LENGTH,
        examples=["Rubin Science Pipelines"],
    ),
]
"""The human title of a documentation site."""


class IntersphinxSourceWriteRequest(BaseModel):
    """Base for the registry's write bodies, which reject unknown fields.

    The observability fields are read-only, and a body that named one and
    came back ``201`` would leave the client believing a claim had been
    recorded that was in fact dropped on the floor. Refusing unknown fields
    turns that into a ``422`` naming the offending field, and catches a
    misspelled ``enabled`` in the same breath instead of applying an
    update that silently changes nothing.
    """

    model_config = ConfigDict(extra="forbid")


class IntersphinxSourceRequest(IntersphinxSourceWriteRequest):
    """A request to register a documentation site with the registry."""

    url: InventoryUrl

    title: SiteTitle

    enabled: Annotated[
        bool,
        Field(
            description=(
                "Whether ingest runs visit this source. A source is"
                " enabled unless it is registered as parked."
            )
        ),
    ] = True


class IntersphinxSourceUpdateRequest(IntersphinxSourceWriteRequest):
    """A request to change a registered source's editable fields.

    Every field is optional and only the fields present are written, so
    retitling a source does not have to restate whether it is enabled. The
    observability fields are absent by design: they are written by ingest
    runs, and a client that could set them could make a source claim a
    success it never had.
    """

    url: InventoryUrl | None = None

    title: SiteTitle | None = None

    enabled: Annotated[
        bool | None,
        Field(description="Whether ingest runs visit this source."),
    ] = None


class IntersphinxSource(BaseModel):
    """A documentation site registered for intersphinx ingest."""

    id: Annotated[
        Base32Id,
        Field(
            description=(
                "The Crockford Base32 identifier of the registration."
            ),
            examples=["1234-5678-90ab-cd2f"],
        ),
    ]

    self_url: Annotated[
        str,
        Field(description="URL to access this registration in the API."),
    ]

    url: Annotated[
        str,
        Field(
            description=(
                "The full URL of the site's ``objects.inv`` inventory."
            ),
            examples=["https://pipelines.lsst.io/objects.inv"],
        ),
    ]

    title: Annotated[
        str,
        Field(
            description="The human title of the documentation site.",
            examples=["Rubin Science Pipelines"],
        ),
    ]

    enabled: Annotated[
        bool,
        Field(description="Whether ingest runs visit this source."),
    ]

    date_ingested: Annotated[
        datetime | None,
        Field(
            description=(
                "When Ook last *attempted* an ingest of this source,"
                " whether it succeeded or failed, or null if it has never"
                " been ingested. Read-only: it is written by ingest runs."
            )
        ),
    ] = None

    last_status: Annotated[
        SourceIngestStatus | None,
        Field(
            description=(
                "The outcome of the most recent ingest attempt, or null if"
                " the source has never been ingested. Read-only: it is"
                " written by ingest runs."
            )
        ),
    ] = None

    last_error: Annotated[
        str | None,
        Field(
            description=(
                "A description of the most recent ingest failure, or null"
                " when the last attempt succeeded or none has been made."
                " Read-only: it is written by ingest runs."
            )
        ),
    ] = None

    @classmethod
    def from_domain(
        cls, source: IntersphinxSourceDomain, *, request: Request
    ) -> Self:
        """Create an `IntersphinxSource` from its domain model."""
        return cls(
            id=source.id,
            self_url=str(
                request.url_for(
                    "get_intersphinx_source",
                    source_id=serialize_ook_base32_id(source.id),
                )
            ),
            url=source.url,
            title=source.title,
            enabled=source.enabled,
            date_ingested=source.date_ingested,
            last_status=source.last_status,
            last_error=source.last_error,
        )
