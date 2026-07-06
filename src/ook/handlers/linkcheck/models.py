"""Models for the linkcheck API."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Annotated

from fastapi import Request
from pydantic import BaseModel, Field

from ook.domain.linkcheck import (
    CheckedUrlReport,
    CheckRunStatus,
    CheckUrlStatus,
    LinkCheckReport,
    SubmittedUrl,
)

__all__ = [
    "CheckedUrl",
    "LinkCheck",
    "LinkCheckRequest",
    "LinkCheckSummary",
    "SubmittedUrlModel",
]


class SubmittedUrlModel(BaseModel):
    """A URL submitted for checking, with the pages it occurs on."""

    url: Annotated[
        str,
        Field(
            description=(
                "The URL to check. Fragments are stripped before"
                " checking; non-http(s) URLs are reported as"
                " unsupported."
            ),
            examples=["https://www.lsst.io/#fragment"],
        ),
    ]

    paths: Annotated[
        list[str],
        Field(
            description=(
                "Page paths where the URL occurs, relative to the"
                " project's documentation root."
            ),
            examples=[["index", "guide/installation"]],
            default_factory=list,
        ),
    ]

    def to_domain(self) -> SubmittedUrl:
        """Convert to the domain submission model."""
        return SubmittedUrl(url=self.url, paths=self.paths)


class LinkCheckRequest(BaseModel):
    """Schema for `post_linkcheck_check`."""

    ltd_slug: Annotated[
        str,
        Field(
            description=(
                "The LSST the Docs project slug the submission is for."
            ),
            examples=["sqr-000"],
        ),
    ]

    default_branch: Annotated[
        bool,
        Field(
            description=(
                "Whether the submission is a default-branch build. Only"
                " default-branch submissions replace the project's"
                " recorded URL occurrences; all submissions receive"
                " full results."
            ),
        ),
    ]

    urls: Annotated[
        list[SubmittedUrlModel],
        Field(description="The URLs to check."),
    ]


class CheckedUrl(BaseModel):
    """The result for one URL within a link check."""

    url: Annotated[
        str,
        Field(description="The canonical (fragment-stripped) URL."),
    ]

    status: Annotated[
        CheckUrlStatus,
        Field(
            description=(
                "The URL's status. ``pending`` URLs are awaiting a"
                " check; other statuses are resolved."
            )
        ),
    ]

    status_code: Annotated[
        int | None,
        Field(
            description=("Final HTTP status code, if a response was received.")
        ),
    ] = None

    redirect_status_code: Annotated[
        int | None,
        Field(
            description=(
                "HTTP status code of the redirect (e.g. 301, 302), if"
                " the URL redirected."
            )
        ),
    ] = None

    redirect_url: Annotated[
        str | None,
        Field(description="Final resolved location, if the URL redirected."),
    ] = None

    error: Annotated[
        str | None,
        Field(description="Description of the failure, if the check failed."),
    ] = None

    checked_at: Annotated[
        datetime | None,
        Field(
            description=(
                "Time of the check that produced this result, or null"
                " while the URL is pending."
            )
        ),
    ] = None

    @classmethod
    def from_domain(cls, report: CheckedUrlReport) -> CheckedUrl:
        """Create a CheckedUrl from a domain per-URL report."""
        return cls(
            url=report.url,
            status=report.status,
            status_code=report.status_code,
            redirect_status_code=report.redirect_status_code,
            redirect_url=report.redirect_url,
            error=report.error,
            checked_at=report.checked_at,
        )


class LinkCheckSummary(BaseModel):
    """Counts of a link check's URLs by status."""

    pending: Annotated[int, Field(description="URLs awaiting a check.")] = 0

    ok: Annotated[
        int, Field(description="URLs that resolve successfully.")
    ] = 0

    redirected: Annotated[
        int,
        Field(description="URLs that work via a permanent redirect."),
    ] = 0

    failing: Annotated[
        int,
        Field(description="URLs currently failing (retry in progress)."),
    ] = 0

    broken: Annotated[int, Field(description="Broken URLs.")] = 0

    unsupported: Annotated[
        int, Field(description="URLs that cannot be checked.")
    ] = 0

    @classmethod
    def from_urls(cls, urls: list[CheckedUrlReport]) -> LinkCheckSummary:
        """Compute summary counts from per-URL reports."""
        counts = Counter(url.status.value for url in urls)
        return cls(**counts)


class LinkCheck(BaseModel):
    """A submitted link check with its per-URL results."""

    id: Annotated[int, Field(description="The check's identifier.")]

    self_url: Annotated[
        str,
        Field(description="URL to access this check in the API."),
    ]

    ltd_slug: Annotated[
        str,
        Field(
            description=(
                "The LSST the Docs project slug the check was submitted for."
            )
        ),
    ]

    default_branch: Annotated[
        bool,
        Field(
            description=("Whether the submission is a default-branch build.")
        ),
    ]

    status: Annotated[
        CheckRunStatus,
        Field(description="The processing status of the check."),
    ]

    date_created: Annotated[
        datetime,
        Field(description="Time the check was submitted."),
    ]

    date_completed: Annotated[
        datetime | None,
        Field(
            description=("Time the check completed, or null while unfinished.")
        ),
    ] = None

    summary: Annotated[
        LinkCheckSummary,
        Field(description="Counts of the check's URLs by status."),
    ]

    urls: Annotated[
        list[CheckedUrl],
        Field(description="Per-URL results, ordered by URL."),
    ]

    @classmethod
    def from_domain(
        cls, report: LinkCheckReport, *, request: Request
    ) -> LinkCheck:
        """Create a LinkCheck from a domain check report."""
        return cls(
            id=report.id,
            self_url=str(
                request.url_for("get_linkcheck_check", check_id=report.id)
            ),
            ltd_slug=report.ltd_slug,
            default_branch=report.default_branch,
            status=report.status,
            date_created=report.date_created,
            date_completed=report.date_completed,
            summary=LinkCheckSummary.from_urls(report.urls),
            urls=[CheckedUrl.from_domain(url) for url in report.urls],
        )
