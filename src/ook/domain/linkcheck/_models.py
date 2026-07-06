"""Domain models for link checking."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field

__all__ = [
    "CheckResult",
    "LinkCheckOutcome",
    "LinkState",
    "LinkStatus",
    "RetryLadderConfig",
    "UrlOccurrence",
]


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
    """An occurrence of a checked URL on a documentation page."""

    url: str = Field(
        description="The canonical (fragment-stripped) URL that occurs."
    )

    path: str = Field(
        description="The page path where the URL occurs, relative to the"
        " project's documentation root."
    )
