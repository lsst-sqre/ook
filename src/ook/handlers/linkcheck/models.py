"""Models for the linkcheck API."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Annotated

from fastapi import Request
from pydantic import AfterValidator, BaseModel, Field, HttpUrl

from ook.domain.base32id import Base32Id, serialize_ook_base32_id
from ook.domain.githuboidc import GitHubOidcClaims
from ook.domain.linkcheck import (
    AcceptedContribution as AcceptedContributionDomain,
)
from ook.domain.linkcheck import (
    CheckedUrlReport,
    CheckRunStatus,
    CheckUrlStatus,
    ContributedResult,
    ContributionProvider,
    ContributionRejectionReason,
    LinkCheckReport,
    LinkStatus,
    ResultSource,
    SubmittedUrl,
    normalize_origin_base_url,
)
from ook.domain.linkcheck import ContributionProvenance as ProvenanceDomain
from ook.domain.linkcheck import ContributionReport as ContributionReportDomain
from ook.domain.linkcheck import OriginLink as OriginLinkDomain
from ook.domain.linkcheck import OriginPage as OriginPageDomain
from ook.domain.linkcheck import (
    RejectedContribution as RejectedContributionDomain,
)
from ook.domain.linkcheck import UrlRecord as UrlRecordDomain

__all__ = [
    "AcceptedContribution",
    "CheckedUrl",
    "ContributedResultModel",
    "ContributionEnvironment",
    "ContributionProvenanceModel",
    "LinkCheck",
    "LinkCheckContributionReport",
    "LinkCheckContributionRequest",
    "LinkCheckRequest",
    "LinkCheckSummary",
    "OriginLink",
    "OriginPage",
    "RejectedContribution",
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

    origin_paths: Annotated[
        list[str],
        Field(
            description=(
                "Page paths where the URL occurs, relative to the"
                " origin's base URL."
            ),
            examples=[["index", "guide/installation"]],
            default_factory=list,
        ),
    ]

    def to_domain(self) -> SubmittedUrl:
        """Convert to the domain submission model."""
        return SubmittedUrl(url=self.url, origin_paths=self.origin_paths)


class LinkCheckRequest(BaseModel):
    """Schema for `post_linkcheck_check`."""

    origin_base_url: Annotated[
        str,
        AfterValidator(normalize_origin_base_url),
        Field(
            description=(
                "The base URL of the website the submission is for."
                " Must be an absolute http(s) URL without a query or"
                " fragment; path-bearing bases are allowed. The host is"
                " lowercased and any trailing slash is stripped."
            ),
            examples=["https://sqr-000.lsst.io"],
        ),
    ]

    is_default_version: Annotated[
        bool,
        Field(
            description=(
                "Whether the submission is a build of the origin's"
                " default version. Only default-version submissions"
                " replace the origin's recorded URL occurrences; all"
                " submissions receive full results."
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

    date_checked: Annotated[
        datetime | None,
        Field(
            description=(
                "Time of the check that produced this result, or null"
                " while the URL is pending."
            )
        ),
    ] = None

    result_source: Annotated[
        ResultSource,
        Field(
            description=(
                "Where this result was observed from: ``server`` for"
                " Ook's own check, ``contribution`` for a result"
                " contributed by a client that checked the URL from its"
                " own vantage point. Pending URLs report ``server``,"
                " having no result yet."
            )
        ),
    ] = ResultSource.server

    contributed_by: Annotated[
        str | None,
        Field(
            description=(
                "The ``owner/name`` of the repository whose CI"
                " contributed this result, or null when Ook checked the"
                " URL itself."
            ),
            examples=["lsst-sqre/documenteer"],
        ),
    ] = None

    origin_paths: Annotated[
        list[str],
        Field(
            description=(
                "Page paths where the URL was submitted in this check,"
                " relative to the origin's base URL."
            ),
            examples=[["index", "guide/installation"]],
        ),
    ]

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
            date_checked=report.date_checked,
            result_source=report.result_source,
            contributed_by=report.contributed_by,
            origin_paths=report.origin_paths,
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

    blocked: Annotated[
        int,
        Field(
            description=(
                "URLs blocked by bot protection (inconclusive; excluded"
                " from the failing and broken counts)."
            )
        ),
    ] = 0

    unsupported: Annotated[
        int, Field(description="URLs that cannot be checked.")
    ] = 0

    @classmethod
    def from_urls(cls, urls: list[CheckedUrlReport]) -> LinkCheckSummary:
        """Compute summary counts from per-URL reports."""
        counts = Counter(url.status.value for url in urls)
        return cls(**counts)


class OriginPage(BaseModel):
    """A page of an origin website where a URL occurs."""

    origin_base_url: Annotated[
        str,
        Field(
            description="The origin website's normalized base URL.",
            examples=["https://sqr-000.lsst.io"],
        ),
    ]

    origin_path: Annotated[
        str,
        Field(
            description=(
                "The page path where the URL occurs, relative to the"
                " origin's base URL."
            ),
            examples=["index"],
        ),
    ]

    @classmethod
    def from_domain(cls, page: OriginPageDomain) -> OriginPage:
        """Create an OriginPage from its domain model."""
        return cls(
            origin_base_url=page.origin_base_url,
            origin_path=page.origin_path,
        )


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

    date_last_checked: Annotated[
        datetime | None,
        Field(
            description=(
                "Time of the most recent check, or null if never checked."
            )
        ),
    ] = None

    date_last_ok: Annotated[
        datetime | None,
        Field(
            description=(
                "Time the URL last resolved successfully, or null if"
                " it has never been seen OK."
            )
        ),
    ] = None

    date_failing_since: Annotated[
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

    date_next_check: Annotated[
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

    result_source: Annotated[
        ResultSource,
        Field(
            description=(
                "Where the most recent result was observed from:"
                " ``server`` for Ook's own check, ``contribution`` for a"
                " result contributed by a client that checked the URL"
                " from its own vantage point. Never-checked URLs report"
                " ``server``."
            )
        ),
    ] = ResultSource.server

    contributed_by: Annotated[
        str | None,
        Field(
            description=(
                "The ``owner/name`` of the repository whose CI"
                " contributed the most recent result, or null when Ook"
                " checked the URL itself."
            ),
            examples=["lsst-sqre/documenteer"],
        ),
    ] = None

    occurrences: Annotated[
        list[OriginPage],
        Field(
            description=(
                "Origin pages where the URL occurs, ordered by origin"
                " base URL and page path."
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
            date_last_checked=record.date_last_checked,
            date_last_ok=record.date_last_ok,
            date_failing_since=record.date_failing_since,
            failure_count=record.failure_count,
            date_next_check=record.date_next_check,
            date_created=record.date_created,
            result_source=record.result_source,
            contributed_by=record.contributed_by,
            occurrences=[
                OriginPage.from_domain(page) for page in record.occurrences
            ],
        )


class OriginLink(BaseModel):
    """A link occurring on an origin website's pages, with its health
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

    date_checked: Annotated[
        datetime | None,
        Field(
            description=(
                "Time of the most recent check, or null if never checked."
            )
        ),
    ] = None

    origin_paths: Annotated[
        list[str],
        Field(
            description=(
                "Page paths on the origin website where the URL occurs,"
                " relative to the origin's base URL."
            ),
            examples=[["index", "guide/installation"]],
        ),
    ]

    @classmethod
    def from_domain(cls, link: OriginLinkDomain) -> OriginLink:
        """Create an OriginLink from its domain model."""
        return cls(
            url=link.url,
            status=link.status,
            status_code=link.status_code,
            redirect_status_code=link.redirect_status_code,
            redirect_url=link.redirect_url,
            error=link.error,
            date_checked=link.date_checked,
            origin_paths=link.origin_paths,
        )


class LinkCheck(BaseModel):
    """A submitted link check with its per-URL results."""

    id: Annotated[Base32Id, Field(description="The check's identifier.")]

    self_url: Annotated[
        str,
        Field(description="URL to access this check in the API."),
    ]

    origin_base_url: Annotated[
        str,
        Field(
            description=(
                "The normalized base URL of the origin website the"
                " check was submitted for."
            )
        ),
    ]

    is_default_version: Annotated[
        bool,
        Field(
            description=(
                "Whether the submission is a build of the origin's"
                " default version."
            )
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
                request.url_for(
                    "get_linkcheck_check",
                    check_id=serialize_ook_base32_id(report.id),
                )
            ),
            origin_base_url=report.origin_base_url,
            is_default_version=report.is_default_version,
            status=report.status,
            date_created=report.date_created,
            date_completed=report.date_completed,
            summary=LinkCheckSummary.from_urls(report.urls),
            urls=[CheckedUrl.from_domain(url) for url in report.urls],
        )


class ContributionEnvironment(BaseModel):
    """The client environment a batch of contributed results came from.

    Every field here is advisory: the identity a contribution is recorded
    under comes from the verified OIDC token, never from the body. A
    ``repository`` that disagrees with the token's claim is ignored.

    Advisory does not mean unconstrained: the descriptive fields are
    persisted verbatim on every contribution row and rendered in reports,
    so each is bounded in length and ``run_url`` must be a URL a report can
    safely link.
    """

    provider: Annotated[
        ContributionProvider,
        Field(
            description=(
                "The kind of client environment the results were observed"
                " from. Only GitHub Actions runs can contribute, because"
                " the provenance attestation is an Actions OIDC id-token."
            )
        ),
    ]

    repository: Annotated[
        str | None,
        Field(
            description=(
                "The ``owner/name`` of the repository the client believes"
                " it is running in. Advisory: the repository recorded as"
                " provenance is the one the id-token attests to."
            ),
            examples=["lsst-sqre/documenteer"],
            max_length=255,
        ),
    ] = None

    run_url: Annotated[
        HttpUrl | None,
        Field(
            description=(
                "A URL to the workflow run, recorded for display in"
                " reports. Must be an http(s) URL: it is rendered as a"
                " link, so a scheme a report could execute is rejected"
                " rather than stored."
            ),
            examples=[
                "https://github.com/lsst-sqre/documenteer/actions/runs/42"
            ],
            max_length=2048,
        ),
    ] = None

    checker_version: Annotated[
        str | None,
        Field(
            description=(
                "The version of the client that performed the checks,"
                " recorded for display in reports."
            ),
            examples=["documenteer 2.1.0"],
            max_length=128,
        ),
    ] = None

    def to_domain(self, claims: GitHubOidcClaims) -> ProvenanceDomain:
        """Combine the attested claims with this advisory environment into
        the domain provenance model.

        The identifying fields come from ``claims`` — the environment block
        never gets to name the repository a contribution is recorded under.
        This is also the one place ``run_url`` crosses from a parsed URL
        back to text, so the domain model, the store column, and the
        response field all keep it as a string.
        """
        return ProvenanceDomain(
            provider=self.provider,
            repository=claims.repository,
            run_id=claims.run_id,
            workflow_ref=claims.workflow_ref,
            run_url=None if self.run_url is None else str(self.run_url),
            checker_version=self.checker_version,
        )


class ContributedResultModel(BaseModel):
    """One URL's result as observed by the contributing client."""

    url: Annotated[
        str,
        Field(
            description=(
                "The URL the client checked. Fragments are stripped before"
                " it is matched against the check's member URLs."
            ),
            examples=["https://example.com/guarded"],
        ),
    ]

    status_code: Annotated[
        int | None,
        Field(
            description=(
                "Final HTTP status code the client received, or null if it"
                " received no response at all. A 2xx resolves the URL;"
                " anything else is a failure."
            ),
            examples=[200],
        ),
    ] = None

    redirect_status_code: Annotated[
        int | None,
        Field(
            description=(
                "HTTP status code of the redirect (e.g. 301, 302), if the"
                " URL redirected. A permanent redirect resolves the URL to"
                " ``redirected`` rather than ``ok``."
            )
        ),
    ] = None

    redirect_url: Annotated[
        str | None,
        Field(description="Final resolved location, if the URL redirected."),
    ] = None

    error: Annotated[
        str | None,
        Field(
            description=(
                "Description of the failure, if the client's check failed."
            )
        ),
    ] = None

    date_checked: Annotated[
        datetime,
        Field(
            description=(
                "Time the client performed the check. Advisory, and"
                " recorded only on the contribution itself: the URL's"
                " state is stamped with the server's receipt time, so"
                " freshness and the retry ladder run on one clock."
            )
        ),
    ]

    def to_domain(self) -> ContributedResult:
        """Convert to the domain contributed-result model."""
        return ContributedResult(
            url=self.url,
            status_code=self.status_code,
            redirect_status_code=self.redirect_status_code,
            redirect_url=self.redirect_url,
            error=self.error,
            date_checked=self.date_checked,
        )


class LinkCheckContributionRequest(BaseModel):
    """Schema for `post_linkcheck_contributions`."""

    id_token: Annotated[
        str,
        Field(
            description=(
                "A GitHub Actions OIDC id-token, minted for this"
                " deployment's audience by the workflow run that observed"
                " the results. Required: it is what attests to where the"
                " results came from. Its ``repository``, ``run_id``, and"
                " ``workflow_ref`` claims are recorded as the"
                " contribution's provenance."
            ),
            min_length=1,
        ),
    ]

    environment: Annotated[
        ContributionEnvironment,
        Field(description="The client environment the results came from."),
    ]

    results: Annotated[
        list[ContributedResultModel],
        Field(
            description=(
                "The per-URL results the client observed. Entries that are"
                " not eligible are rejected individually; the rest still"
                " apply. A batch may carry at most as many results as a"
                " check submission may carry URLs"
                " (``OOK_LINKCHECK_MAX_URLS_PER_CHECK``); a larger one is"
                " rejected whole."
            ),
            min_length=1,
        ),
    ]


class ContributionProvenanceModel(BaseModel):
    """Where a batch of contributed results was observed from."""

    provider: Annotated[
        ContributionProvider,
        Field(description="The kind of client environment."),
    ]

    repository: Annotated[
        str,
        Field(
            description=(
                "The ``owner/name`` of the repository whose CI observed"
                " the results, from the verified id-token."
            ),
            examples=["lsst-sqre/documenteer"],
        ),
    ]

    run_id: Annotated[
        str,
        Field(
            description=(
                "The workflow run that observed the results, from the"
                " verified id-token."
            ),
            examples=["42"],
        ),
    ]

    workflow_ref: Annotated[
        str,
        Field(
            description=(
                "The fully-qualified reference of the workflow that"
                " observed the results, from the verified id-token."
            ),
            examples=[
                "lsst-sqre/documenteer/.github/workflows/ci.yaml@refs/heads/main"
            ],
        ),
    ]

    run_url: Annotated[
        str | None,
        Field(description="The run URL the client reported, if any."),
    ] = None

    checker_version: Annotated[
        str | None,
        Field(description="The client version the client reported, if any."),
    ] = None

    @classmethod
    def from_domain(
        cls, provenance: ProvenanceDomain
    ) -> ContributionProvenanceModel:
        """Create a provenance block from its domain model."""
        return cls(
            provider=provenance.provider,
            repository=provenance.repository,
            run_id=provenance.run_id,
            workflow_ref=provenance.workflow_ref,
            run_url=provenance.run_url,
            checker_version=provenance.checker_version,
        )


class AcceptedContribution(BaseModel):
    """A contributed result that was applied to its URL."""

    url: Annotated[
        str,
        Field(description="The canonical URL the result was applied to."),
    ]

    status: Annotated[
        LinkStatus,
        Field(
            description=(
                "The URL's status after the contributed result ran through"
                " the status-transition engine."
            )
        ),
    ]

    @classmethod
    def from_domain(
        cls, accepted: AcceptedContributionDomain
    ) -> AcceptedContribution:
        """Create an accepted entry from its domain model."""
        return cls(url=accepted.url, status=accepted.status)


class RejectedContribution(BaseModel):
    """A contributed result that was not applied, and why."""

    url: Annotated[
        str,
        Field(description="The URL as it was submitted."),
    ]

    reason: Annotated[
        ContributionRejectionReason,
        Field(
            description=(
                "Why the result was not applied: ``not_a_member`` if the"
                " URL is not part of this check, ``not_blocked`` if Ook's"
                " own verdict for the URL stands (it is neither"
                " ``blocked`` nor ``broken`` without a response, so Ook"
                " reached that verdict from its own vantage point),"
                " ``unsupported_url`` if the URL cannot be checked at all,"
                " and ``duplicate`` if an earlier entry in the same batch"
                " already contributed a result for it. The accompanying"
                " ``message`` names the URL's current status."
            )
        ),
    ]

    message: Annotated[
        str,
        Field(description="A human-readable explanation of the rejection."),
    ]

    @classmethod
    def from_domain(
        cls, rejected: RejectedContributionDomain
    ) -> RejectedContribution:
        """Create a rejected entry from its domain model."""
        return cls(
            url=rejected.url,
            reason=rejected.reason,
            message=rejected.message,
        )


class LinkCheckContributionReport(BaseModel):
    """The outcome of a batch of contributed results."""

    check_id: Annotated[
        Base32Id,
        Field(description="The check the results were contributed against."),
    ]

    provenance: Annotated[
        ContributionProvenanceModel,
        Field(
            description=(
                "Where the results were recorded as coming from, taken"
                " from the verified id-token."
            )
        ),
    ]

    accepted: Annotated[
        list[AcceptedContribution],
        Field(
            description=(
                "The results that were applied, with the status each URL"
                " reached, in submission order."
            )
        ),
    ]

    rejected: Annotated[
        list[RejectedContribution],
        Field(
            description=(
                "The results that were not applied, each with its reason,"
                " in submission order."
            )
        ),
    ]

    @classmethod
    def from_domain(
        cls, report: ContributionReportDomain
    ) -> LinkCheckContributionReport:
        """Create a contribution report from its domain model."""
        return cls(
            check_id=report.check_id,
            provenance=ContributionProvenanceModel.from_domain(
                report.provenance
            ),
            accepted=[
                AcceptedContribution.from_domain(entry)
                for entry in report.accepted
            ],
            rejected=[
                RejectedContribution.from_domain(entry)
                for entry in report.rejected
            ],
        )
