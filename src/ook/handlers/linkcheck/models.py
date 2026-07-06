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
from ook.domain.linkcheck import ProjectLink as ProjectLinkDomain
from ook.domain.linkcheck import ProjectPage as ProjectPageDomain
from ook.domain.linkcheck import UrlRecord as UrlRecordDomain

__all__ = [
    "CheckedUrl",
    "LinkCheck",
    "LinkCheckRequest",
    "LinkCheckSummary",
    "ProjectLink",
    "ProjectPage",
    "SubmittedUrlModel",
    "UrlRecord",
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


class ProjectPage(BaseModel):
    """A documentation page of an LTD project where a URL occurs."""

    ltd_slug: Annotated[
        str,
        Field(
            description="The LSST the Docs project slug.",
            examples=["sqr-000"],
        ),
    ]

    path: Annotated[
        str,
        Field(
            description=(
                "The page path where the URL occurs, relative to the"
                " project's documentation root."
            ),
            examples=["index"],
        ),
    ]

    @classmethod
    def from_domain(cls, page: ProjectPageDomain) -> ProjectPage:
        """Create a ProjectPage from its domain model."""
        return cls(ltd_slug=page.ltd_slug, path=page.path)


class UrlRecord(BaseModel):
    """The stored health record of a checked URL."""

    url: Annotated[
        str,
        Field(description="The canonical (fragment-stripped) URL."),
    ]

    status: Annotated[
        CheckUrlStatus,
        Field(
            description=(
                "The URL's health status; ``pending`` if the URL has"
                " never been checked."
            )
        ),
    ]

    status_code: Annotated[
        int | None,
        Field(
            description=(
                "Final HTTP status code from the most recent check, if"
                " a response was received."
            )
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
        Field(
            description=(
                "Final resolved location, if the URL redirected. For"
                " permanent redirects this is the location the source"
                " should be updated to."
            )
        ),
    ] = None

    error: Annotated[
        str | None,
        Field(
            description=(
                "Description of the failure from the most recent"
                " check, if it failed."
            )
        ),
    ] = None

    last_checked_at: Annotated[
        datetime | None,
        Field(
            description=(
                "Time of the most recent check, or null if never checked."
            )
        ),
    ] = None

    last_ok_at: Annotated[
        datetime | None,
        Field(
            description=(
                "Time the URL last resolved successfully, or null if"
                " it has never been seen OK."
            )
        ),
    ] = None

    failing_since: Annotated[
        datetime | None,
        Field(
            description=(
                "Start of the current consecutive-failure streak, or"
                " null if the URL is not failing."
            )
        ),
    ] = None

    failure_count: Annotated[
        int,
        Field(
            description=(
                "Number of consecutive failed checks in the current streak."
            )
        ),
    ] = 0

    next_check_at: Annotated[
        datetime | None,
        Field(
            description=(
                "Time of the next scheduled recheck on the retry"
                " ladder, or null if the URL is not on the ladder."
            )
        ),
    ] = None

    date_created: Annotated[
        datetime,
        Field(description="Time the URL's record was created."),
    ]

    occurrences: Annotated[
        list[ProjectPage],
        Field(
            description=(
                "Project pages where the URL occurs, ordered by"
                " project slug and page path."
            )
        ),
    ]

    @classmethod
    def from_domain(cls, record: UrlRecordDomain) -> UrlRecord:
        """Create a UrlRecord from its domain model."""
        return cls(
            url=record.url,
            status=record.status,
            status_code=record.status_code,
            redirect_status_code=record.redirect_status_code,
            redirect_url=record.redirect_url,
            error=record.error,
            last_checked_at=record.last_checked_at,
            last_ok_at=record.last_ok_at,
            failing_since=record.failing_since,
            failure_count=record.failure_count,
            next_check_at=record.next_check_at,
            date_created=record.date_created,
            occurrences=[
                ProjectPage.from_domain(page) for page in record.occurrences
            ],
        )


class ProjectLink(BaseModel):
    """A link occurring in a project's documentation, with its health
    state.
    """

    url: Annotated[
        str,
        Field(description="The canonical (fragment-stripped) URL."),
    ]

    status: Annotated[
        CheckUrlStatus,
        Field(
            description=(
                "The URL's health status; ``pending`` if the URL has"
                " never been checked."
            )
        ),
    ]

    status_code: Annotated[
        int | None,
        Field(
            description=(
                "Final HTTP status code from the most recent check, if"
                " a response was received."
            )
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
        Field(
            description=(
                "Final resolved location, if the URL redirected. For"
                " permanent redirects this is the location the source"
                " should be updated to."
            )
        ),
    ] = None

    error: Annotated[
        str | None,
        Field(
            description=(
                "Description of the failure from the most recent"
                " check, if it failed."
            )
        ),
    ] = None

    checked_at: Annotated[
        datetime | None,
        Field(
            description=(
                "Time of the most recent check, or null if never checked."
            )
        ),
    ] = None

    paths: Annotated[
        list[str],
        Field(
            description=(
                "Page paths in the project where the URL occurs,"
                " relative to the project's documentation root."
            ),
            examples=[["index", "guide/installation"]],
        ),
    ]

    @classmethod
    def from_domain(cls, link: ProjectLinkDomain) -> ProjectLink:
        """Create a ProjectLink from its domain model."""
        return cls(
            url=link.url,
            status=link.status,
            status_code=link.status_code,
            redirect_status_code=link.redirect_status_code,
            redirect_url=link.redirect_url,
            error=link.error,
            checked_at=link.checked_at,
            paths=link.paths,
        )


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
