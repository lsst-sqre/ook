"""Domain models for link checking."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field

from ook.domain.base32id import Base32Id

__all__ = [
    "CheckResult",
    "CheckRunStatus",
    "CheckUrlStatus",
    "CheckedUrlReport",
    "LinkCheckOutcome",
    "LinkCheckReport",
    "LinkState",
    "LinkStatus",
    "OriginLink",
    "OriginPage",
    "RetryLadderConfig",
    "SubmittedUrl",
    "UrlOccurrence",
    "UrlRecord",
]


class CheckRunStatus(StrEnum):
    """The processing status of a submitted link check."""

    pending = "pending"
    """The check has been accepted but execution has not started."""

    in_progress = "in_progress"
    """The check's due URLs are being checked."""

    complete = "complete"
    """All of the check's URLs have resolved statuses."""


class LinkStatus(StrEnum):
    """The health status of an external link."""

    ok = "ok"
    """The link resolves successfully."""

    redirected = "redirected"
    """The link works via a permanent redirect; the source should be
    updated to the recorded final location.
    """

    failing = "failing"
    """A previously-OK link is currently failing; the retry ladder is
    in progress. Reported to clients as a warning.
    """

    broken = "broken"
    """The retry ladder is exhausted, or a link never seen OK failed."""

    unsupported = "unsupported"
    """The URL cannot be checked (non-http(s) scheme or malformed)."""


class CheckResult(StrEnum):
    """The raw result of a single check attempt of a URL."""

    success = "success"
    """The URL resolved to a successful response."""

    failure = "failure"
    """The URL failed to resolve (HTTP error or network failure)."""

    unsupported = "unsupported"
    """The URL cannot be checked at all."""


class RetryLadderConfig(BaseModel):
    """Parameterized thresholds for the failing-to-broken retry ladder.

    The engine takes these thresholds from the caller so that the
    service layer can bind them to application configuration.
    """

    broken_threshold: timedelta = Field(
        timedelta(hours=48),
        description=(
            "Minimum span of consecutive failures before a"
            " previously-OK link is declared broken."
        ),
    )

    min_attempts: int = Field(
        3,
        ge=1,
        description=(
            "Minimum number of consecutive failed attempts before a"
            " previously-OK link is declared broken."
        ),
    )

    recheck_intervals: tuple[timedelta, ...] = Field(
        (
            timedelta(hours=1),
            timedelta(hours=4),
            timedelta(hours=24),
            timedelta(hours=48),
        ),
        min_length=1,
        description=(
            "Delays until the next recheck of a failing link, indexed"
            " by the number of consecutive failures so far. The last"
            " interval repeats when the ladder is longer than this"
            " schedule."
        ),
    )

    broken_recheck_interval: timedelta = Field(
        timedelta(hours=24),
        description=(
            "Delay until the next recheck of a broken link. Broken"
            " links are revisited at this slow cadence so a link that"
            " has since been fixed can heal back to ok/redirected"
            " without waiting to be resubmitted."
        ),
    )


class LinkCheckOutcome(BaseModel):
    """The outcome of a single check of a URL.

    This is the engine's input: a description of what happened when a
    checker attempted to resolve the URL. Producing an outcome (HTTP
    I/O) is the responsibility of a separate service.
    """

    checked_at: datetime = Field(
        description="Time when the check was performed."
    )

    result: CheckResult = Field(description="The raw result of the check.")

    status_code: int | None = Field(
        None,
        description="Final HTTP status code, if a response was received.",
    )

    redirect_status_code: int | None = Field(
        None,
        description=(
            "HTTP status code of the redirect (e.g. 301, 302, 307,"
            " 308), if the URL redirected."
        ),
    )

    redirect_url: str | None = Field(
        None,
        description="Final resolved location, if the URL redirected.",
    )

    error: str | None = Field(
        None,
        description="Description of the failure, if the check failed.",
    )


class LinkState(BaseModel):
    """The health state of a link, as tracked across checks.

    This is both the engine's prior-state input and its output.
    """

    url: str = Field(description="The checked URL.")

    status: LinkStatus = Field(description="Current health status.")

    checked_at: datetime = Field(description="Time of the most recent check.")

    last_ok_at: datetime | None = Field(
        None,
        description=(
            "Time the link last resolved successfully, or None if it"
            " has never been seen OK."
        ),
    )

    failing_since: datetime | None = Field(
        None,
        description=(
            "Start of the current consecutive-failure streak, or None"
            " if the link is not failing."
        ),
    )

    failure_count: int = Field(
        0,
        ge=0,
        description=(
            "Number of consecutive failed checks in the current streak."
        ),
    )

    status_code: int | None = Field(
        None,
        description=(
            "HTTP status code from the most recent check, if a"
            " response was received."
        ),
    )

    redirect_status_code: int | None = Field(
        None,
        description=(
            "HTTP status code of the redirect, if the most recent"
            " check succeeded via a redirect."
        ),
    )

    redirect_url: str | None = Field(
        None,
        description=(
            "Final resolved location, if the most recent check"
            " succeeded via a redirect. For permanent redirects this"
            " is the location the source should be updated to."
        ),
    )

    error: str | None = Field(
        None,
        description=(
            "Description of the failure from the most recent check,"
            " if it failed."
        ),
    )

    next_check_at: datetime | None = Field(
        None,
        description=(
            "Time of the next scheduled recheck on the retry ladder,"
            " or None if the link is not on the ladder."
        ),
    )


class UrlOccurrence(BaseModel):
    """An occurrence of a checked URL on a page of an origin website."""

    url: str = Field(
        description="The canonical (fragment-stripped) URL that occurs."
    )

    origin_path: str = Field(
        description="The page path where the URL occurs, relative to the"
        " origin's base URL."
    )


class SubmittedUrl(BaseModel):
    """A URL submitted for checking, with the pages it occurs on."""

    url: str = Field(
        description=(
            "The URL as submitted (any scheme; not yet canonicalized)."
        )
    )

    origin_paths: list[str] = Field(
        default_factory=list,
        description=(
            "The page paths where the URL occurs, relative to the"
            " origin's base URL."
        ),
    )


class CheckUrlStatus(StrEnum):
    """The reported status of a URL within a submitted link check.

    Extends `LinkStatus` with ``pending`` for URLs whose check has not
    completed yet.
    """

    pending = "pending"
    """The URL is due for a check that has not completed yet."""

    ok = "ok"
    """The link resolves successfully."""

    redirected = "redirected"
    """The link works via a permanent redirect."""

    failing = "failing"
    """The link is currently failing; the retry ladder is in progress."""

    broken = "broken"
    """The link is broken."""

    unsupported = "unsupported"
    """The URL cannot be checked."""

    @classmethod
    def from_link_status(cls, status: LinkStatus) -> CheckUrlStatus:
        """Convert a `LinkStatus` to the equivalent check-URL status."""
        return cls(status.value)


class CheckedUrlReport(BaseModel):
    """The reported result for one URL within a submitted link check."""

    url: str = Field(description="The canonical (fragment-stripped) URL.")

    status: CheckUrlStatus = Field(description="The URL's reported status.")

    status_code: int | None = Field(
        None,
        description="Final HTTP status code, if a response was received.",
    )

    redirect_status_code: int | None = Field(
        None,
        description="HTTP status code of the redirect, if redirected.",
    )

    redirect_url: str | None = Field(
        None,
        description="Final resolved location, if the URL redirected.",
    )

    error: str | None = Field(
        None,
        description="Description of the failure, if the check failed.",
    )

    checked_at: datetime | None = Field(
        None,
        description=(
            "Time of the check that produced this result, or None while"
            " the URL is pending."
        ),
    )

    origin_paths: list[str] = Field(
        default_factory=list,
        description=(
            "The origin page paths this URL was submitted with in this"
            " check, sorted and de-duplicated."
        ),
    )


class OriginPage(BaseModel):
    """A page of an origin website where a URL occurs."""

    origin_base_url: str = Field(
        description="The origin's normalized base URL."
    )

    origin_path: str = Field(
        description=(
            "The page path where the URL occurs, relative to the"
            " origin's base URL."
        )
    )


class UrlRecord(BaseModel):
    """The stored health record of a checked URL, for the query API."""

    url: str = Field(description="The canonical (fragment-stripped) URL.")

    status: CheckUrlStatus = Field(
        description=(
            "The URL's health status; ``pending`` if the URL has never"
            " been checked."
        )
    )

    status_code: int | None = Field(
        None,
        description=(
            "Final HTTP status code from the most recent check, if a"
            " response was received."
        ),
    )

    redirect_status_code: int | None = Field(
        None,
        description="HTTP status code of the redirect, if redirected.",
    )

    redirect_url: str | None = Field(
        None,
        description="Final resolved location, if the URL redirected.",
    )

    error: str | None = Field(
        None,
        description=(
            "Description of the failure from the most recent check,"
            " if it failed."
        ),
    )

    last_checked_at: datetime | None = Field(
        None,
        description=(
            "Time of the most recent check, or None if never checked."
        ),
    )

    last_ok_at: datetime | None = Field(
        None,
        description=(
            "Time the URL last resolved successfully, or None if it"
            " has never been seen OK."
        ),
    )

    failing_since: datetime | None = Field(
        None,
        description=(
            "Start of the current consecutive-failure streak, or None"
            " if the URL is not failing."
        ),
    )

    failure_count: int = Field(
        0,
        ge=0,
        description=(
            "Number of consecutive failed checks in the current streak."
        ),
    )

    next_check_at: datetime | None = Field(
        None,
        description=(
            "Time of the next scheduled recheck on the retry ladder,"
            " or None if the URL is not on the ladder."
        ),
    )

    date_created: datetime = Field(
        description="Time the URL's record was created."
    )

    occurrences: list[OriginPage] = Field(
        default_factory=list,
        description=(
            "Origin pages where the URL occurs, ordered by origin base"
            " URL and page path."
        ),
    )


class OriginLink(BaseModel):
    """A link occurring on an origin website's pages, with its health
    state.
    """

    url: str = Field(description="The canonical (fragment-stripped) URL.")

    status: CheckUrlStatus = Field(
        description=(
            "The URL's health status; ``pending`` if the URL has never"
            " been checked."
        )
    )

    status_code: int | None = Field(
        None,
        description=(
            "Final HTTP status code from the most recent check, if a"
            " response was received."
        ),
    )

    redirect_status_code: int | None = Field(
        None,
        description="HTTP status code of the redirect, if redirected.",
    )

    redirect_url: str | None = Field(
        None,
        description=(
            "Final resolved location, if the URL redirected. For"
            " permanent redirects this is the location the source"
            " should be updated to."
        ),
    )

    error: str | None = Field(
        None,
        description=(
            "Description of the failure from the most recent check,"
            " if it failed."
        ),
    )

    checked_at: datetime | None = Field(
        None,
        description=(
            "Time of the most recent check, or None if never checked."
        ),
    )

    origin_paths: list[str] = Field(
        description=(
            "Page paths on the origin website where the URL occurs,"
            " relative to the origin's base URL."
        )
    )


class LinkCheckReport(BaseModel):
    """The status report for a submitted link check."""

    id: Base32Id = Field(description="The check's identifier.")

    origin_base_url: str = Field(
        description=(
            "The normalized base URL of the origin website the check"
            " was submitted for."
        )
    )

    is_default_version: bool = Field(
        description=(
            "Whether the submission is a build of the origin's default"
            " version."
        )
    )

    status: CheckRunStatus = Field(
        description="The processing status of the check."
    )

    date_created: datetime = Field(
        description="The time the check was submitted."
    )

    date_completed: datetime | None = Field(
        None,
        description="The time the check completed, or None while unfinished.",
    )

    urls: list[CheckedUrlReport] = Field(
        description="Per-URL results, ordered by URL."
    )
