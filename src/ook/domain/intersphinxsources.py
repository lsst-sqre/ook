"""Domain models for the registry of intersphinx documentation sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

__all__ = ["IntersphinxSource", "SourceIngestStatus"]


class SourceIngestStatus(StrEnum):
    """The outcome of the most recent ingest of a documentation source."""

    success = "success"
    """The source's inventory was fetched, parsed, and its links replaced."""

    failure = "failure"
    """The source's inventory could not be fetched or parsed.

    The links from the last successful ingest are kept: a site that is
    briefly unreachable should not blank out the links Ook already serves
    for it.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class IntersphinxSource:
    """A registered documentation site whose inventory Ook ingests."""

    id: int
    """The source's database ID.

    Part of the model rather than a store implementation detail because a
    link row points at the source by ID, so the ingest path needs it.
    """

    url: str
    """The full URL of the site's ``objects.inv`` inventory, which is the
    source's identity.
    """

    title: str
    """The human title of the documentation site, which surfaces as the
    ``collection_title`` of every link ingested from it.
    """

    enabled: bool
    """Whether ingest runs visit this source."""

    date_ingested: datetime | None
    """The time of the most recent ingest attempt, successful or not, or
    None if the source has never been ingested.
    """

    last_status: SourceIngestStatus | None
    """The outcome of the most recent ingest attempt, or None if the source
    has never been ingested.
    """

    last_error: str | None
    """A description of the most recent ingest failure, or None when the
    last attempt succeeded or none has been made.
    """
