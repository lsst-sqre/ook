"""Service for orchestrating submitted link checks."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ook.domain.linkcheck import (
    AcceptedContribution,
    CheckedUrlReport,
    CheckRunStatus,
    CheckUrlStatus,
    ContributionRejectionReason,
    ContributionReport,
    LinkCheckReport,
    LinkState,
    LinkStatus,
    OriginLink,
    RejectedContribution,
    SubmittedUrl,
    UrlOccurrence,
    UrlRecord,
    accepts_contribution,
    canonicalize_url,
    contributed_outcome,
    evaluate_outcome,
    is_supported_url,
)
from ook.exceptions import LinkCheckTooManyUrlsError
from ook.storage.linkcheckstore import DueUrl

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from safir.database import CountedPaginatedList
    from structlog.stdlib import BoundLogger

    from ook.domain.linkcheck import (
        ContributedResult,
        ContributionProvenance,
        RetryLadderConfig,
    )
    from ook.storage.linkcheckstore import (
        CheckUrlRecord,
        LinkCheckStore,
        OriginLinksCursor,
    )

    from ._urlchecker import UrlChecker

__all__ = ["LinkCheckService", "PurgeResult", "SubmittedCheck"]


@dataclass(frozen=True, slots=True)
class PurgeResult:
    """The result of purging expired link-check records."""

    check_count: int
    """The number of purged expired checks."""

    url_count: int
    """The number of purged orphaned URL records."""


@dataclass(frozen=True, slots=True)
class SubmittedCheck:
    """The result of accepting a link-check submission."""

    check_id: int
    """The identifier of the created check."""

    due_urls: list[DueUrl]
    """The member URLs that need a (re)check to complete this check.

    This is the seam for the execution and enqueueing slices: cached-
    fresh and unsupported URLs are already resolved and are not listed.
    """


class LinkCheckService:
    """Orchestrates link-check submissions and status reports.

    Parameters
    ----------
    linkcheck_store
        Store for link-check records.
    logger
        A logger.
    freshness_ttl
        Age below which a URL's stored check result is fresh; fresh
        results are reported immediately instead of being rechecked.
    max_urls_per_check
        Maximum number of unique canonical URLs accepted per submission.
    url_checker
        The HTTP checker used to resolve due URLs during execution.
    retry_ladder
        Thresholds for the failing-to-broken retry ladder, bound to
        application configuration by the factory.
    check_retention
        Age beyond which check records are purged by the scheduled
        recheck maintenance.
    """

    def __init__(
        self,
        *,
        linkcheck_store: LinkCheckStore,
        logger: BoundLogger,
        freshness_ttl: timedelta,
        max_urls_per_check: int,
        url_checker: UrlChecker,
        retry_ladder: RetryLadderConfig,
        check_retention: timedelta,
    ) -> None:
        self._store = linkcheck_store
        self._logger = logger
        self._freshness_ttl = freshness_ttl
        self._max_urls_per_check = max_urls_per_check
        self._url_checker = url_checker
        self._retry_ladder = retry_ladder
        self._check_retention = check_retention

    async def submit_check(
        self,
        *,
        origin_base_url: str,
        is_default_version: bool,
        urls: Sequence[SubmittedUrl],
    ) -> SubmittedCheck:
        """Accept a link-check submission.

        URLs are canonicalized (fragments stripped) and partitioned:
        unsupported URLs resolve immediately, URLs with a fresh cached
        result keep it, and the rest are due for a check. The check and
        its URL membership are persisted; for default-version
        submissions the origin's occurrence set is replaced. A check
        with no due URLs is already fully resolved and is marked
        complete immediately, since no execution is enqueued for it.

        Parameters
        ----------
        origin_base_url
            The normalized base URL of the origin website the check is
            submitted for.
        is_default_version
            Whether the submission is a build of the origin's default
            version. Only default-version submissions replace the
            origin's occurrence set; all submissions receive full
            results.
        urls
            The submitted URLs with the page paths they occur on.

        Returns
        -------
        SubmittedCheck
            The created check's ID and its due URLs.

        Raises
        ------
        ook.exceptions.LinkCheckTooManyUrlsError
            Raised if the number of unique canonical URLs exceeds the
            per-check cap.
        """
        now = datetime.now(tz=UTC)

        # Canonicalize URLs and merge their page paths, preserving
        # submission order.
        merged_paths: dict[str, list[str]] = {}
        for submitted in urls:
            canonical = canonicalize_url(submitted.url)
            paths = merged_paths.setdefault(canonical, [])
            for path in submitted.origin_paths:
                if path not in paths:
                    paths.append(path)

        if len(merged_paths) > self._max_urls_per_check:
            raise LinkCheckTooManyUrlsError(
                f"Link-check submission has {len(merged_paths)} unique"
                f" URLs, which exceeds the per-check limit of"
                f" {self._max_urls_per_check}."
            )

        canonical_urls = list(merged_paths)
        url_ids = await self._store.upsert_checked_urls(
            canonical_urls, now=now
        )

        # Unsupported URLs resolve immediately at submission.
        supported_urls = []
        for url in canonical_urls:
            if is_supported_url(url):
                supported_urls.append(url)
            else:
                await self._store.upsert_url_state(
                    LinkState(
                        url=url,
                        status=LinkStatus.unsupported,
                        date_checked=now,
                    ),
                    now=now,
                )

        # Partition supported URLs into cached-fresh vs due.
        states = await self._store.get_url_states(supported_urls)
        due_urls = [
            DueUrl(id=url_ids[url], url=url)
            for url in supported_urls
            if self._is_due(states.get(url), now)
        ]

        check_id = await self._store.create_check(
            origin_base_url=origin_base_url,
            is_default_version=is_default_version,
            checked_url_ids=[url_ids[url] for url in canonical_urls],
            origin_paths={
                url_ids[url]: paths for url, paths in merged_paths.items()
            },
            now=now,
        )
        if not due_urls:
            # Nothing to execute, and only execution advances a check's
            # status, so an all-fresh check completes at submission.
            await self._store.update_check_status(
                check_id, CheckRunStatus.complete, now=now
            )

        if is_default_version:
            occurrences = [
                UrlOccurrence(url=url, origin_path=path)
                for url, paths in merged_paths.items()
                for path in paths
            ]
            await self._store.replace_origin_occurrences(
                origin_base_url=origin_base_url,
                occurrences=occurrences,
                now=now,
            )

        self._logger.info(
            "Accepted link-check submission",
            check_id=check_id,
            origin_base_url=origin_base_url,
            is_default_version=is_default_version,
            url_count=len(canonical_urls),
            due_url_count=len(due_urls),
        )
        return SubmittedCheck(check_id=check_id, due_urls=due_urls)

    async def execute_check(self, check_id: int) -> None:
        """Execute a submitted link check.

        The check's member URLs that are due for a (re)check are
        resolved with the URL checker (under its global concurrency cap
        and per-host politeness schedule), their states are advanced
        through the status-transition engine and persisted, and the
        check moves through ``in_progress`` to ``complete``.

        Parameters
        ----------
        check_id
            The identifier of the check to execute, as returned by
            `submit_check`.
        """
        record = await self._store.get_check(check_id)
        if record is None:
            # Execution requests may be delivered at-least-once and can
            # outlive their check, so an unknown id is not an error.
            self._logger.warning(
                "Skipped execution of unknown link check", check_id=check_id
            )
            return
        if record.status is CheckRunStatus.complete:
            # ``complete`` is the only terminal status, so a redelivered
            # execution request for a completed check is a no-op:
            # re-checking would flip its status back to ``in_progress``
            # and leave a stale ``date_completed`` during the re-run.
            self._logger.info(
                "Skipped re-execution of completed link check",
                check_id=check_id,
            )
            return
        await self._store.update_check_status(
            check_id, CheckRunStatus.in_progress
        )

        now = datetime.now(tz=UTC)
        supported_urls = [
            url_record.url
            for url_record in record.urls
            if is_supported_url(url_record.url)
        ]
        states = await self._store.get_url_states(supported_urls)
        urls = [
            url for url in supported_urls if self._is_due(states.get(url), now)
        ]

        outcomes = await asyncio.gather(
            *(self._url_checker.check(url) for url in urls)
        )
        for url, outcome in zip(urls, outcomes, strict=True):
            state = evaluate_outcome(
                url=url,
                prior=states.get(url),
                outcome=outcome,
                ladder=self._retry_ladder,
            )
            await self._store.upsert_url_state(state)

        await self._store.update_check_status(
            check_id, CheckRunStatus.complete
        )
        self._logger.info(
            "Completed link check",
            check_id=check_id,
            checked_url_count=len(urls),
        )

    async def execute_recheck(self, url_ids: Sequence[int]) -> None:
        """Execute a scheduled recheck of stored URLs.

        The URLs that are still due are resolved with the URL checker
        (under its global concurrency cap and per-host politeness
        schedule) and their states are advanced through the
        status-transition engine — this is how a failing link's retry
        ladder progresses to broken over subsequent days.

        Parameters
        ----------
        url_ids
            The ``checked_url`` primary keys to recheck, as enqueued by
            the scheduled recheck. Recheck requests are delivered at
            least once and may outlive their URL records, so unknown
            ids are skipped; URLs whose results have been refreshed
            since enqueueing are skipped as no longer due.
        """
        records = await self._store.get_urls_by_ids(url_ids)
        if len(records) < len(set(url_ids)):
            self._logger.warning(
                "Skipped recheck of unknown URL ids",
                unknown_count=len(set(url_ids)) - len(records),
            )

        now = datetime.now(tz=UTC)
        supported_urls = [
            record.url for record in records if is_supported_url(record.url)
        ]
        states = await self._store.get_url_states(supported_urls)
        urls = [
            url for url in supported_urls if self._is_due(states.get(url), now)
        ]

        outcomes = await asyncio.gather(
            *(self._url_checker.check(url) for url in urls)
        )
        for url, outcome in zip(urls, outcomes, strict=True):
            state = evaluate_outcome(
                url=url,
                prior=states.get(url),
                outcome=outcome,
                ladder=self._retry_ladder,
            )
            await self._store.upsert_url_state(state)

        self._logger.info(
            "Completed link recheck", checked_url_count=len(urls)
        )

    async def contribute_results(
        self,
        *,
        check_id: int,
        provenance: ContributionProvenance,
        results: Sequence[ContributedResult],
    ) -> ContributionReport | None:
        """Apply client-contributed results to a check's unverified URLs.

        A client that re-checked a URL Ook's own egress could not verify —
        one Ook found bot-blocked, or found broken without ever getting a
        response — can contribute what it saw. Eligible results run through
        the same status-transition engine and retry ladder as Ook's own
        checks, so a contributed success resolves the URL and a contributed
        failure advances its ladder; only the recorded vantage point
        differs. Each applied result is also recorded as a contribution row,
        the append-only provenance trail behind the state it produced.

        The batch is applied entry by entry: ineligible entries are
        reported back with a reason while the rest still apply.

        Parameters
        ----------
        check_id
            The check the results were contributed against.
        provenance
            Where the results were observed from. Its identifying claims
            come from a verified OIDC token, not the request body.
        results
            The per-URL results the client observed.

        Returns
        -------
        ContributionReport or None
            What was applied and what was rejected, or None if the check
            is unknown.
        """
        record = await self._store.get_check(check_id)
        if record is None:
            return None

        now = datetime.now(tz=UTC)
        members = {url.url for url in record.urls}
        # The check record establishes membership only; eligibility is
        # judged from these state rows, which are also what each write
        # advances. Reading them once means a URL a concurrent server
        # check resolves in between cannot be judged eligible from one
        # snapshot and then written from another.
        states = await self._store.get_url_states(
            self._contribution_candidates(results, members=members)
        )
        eligible, rejected = self._partition_contributions(
            results, members=members, states=states
        )

        accepted: list[AcceptedContribution] = []
        for result in eligible:
            state = evaluate_outcome(
                url=result.url,
                prior=states.get(result.url),
                outcome=contributed_outcome(
                    result, repository=provenance.repository, received_at=now
                ),
                ladder=self._retry_ladder,
            )
            await self._store.upsert_url_state(state, now=now)
            accepted.append(
                AcceptedContribution(url=result.url, status=state.status)
            )
        await self._store.add_contributions(
            check_id=check_id,
            provenance=provenance,
            results=eligible,
            now=now,
        )

        self._logger.info(
            "Applied link-check contributions",
            check_id=check_id,
            repository=provenance.repository,
            run_id=provenance.run_id,
            accepted_count=len(accepted),
            rejected_count=len(rejected),
        )
        if rejected:
            self._logger.info(
                "Rejected link-check contributions",
                check_id=check_id,
                repository=provenance.repository,
                rejected_count=len(rejected),
                reasons=dict(
                    Counter(entry.reason.value for entry in rejected)
                ),
            )
        return ContributionReport(
            check_id=check_id,
            provenance=provenance,
            accepted=accepted,
            rejected=rejected,
        )

    @staticmethod
    def _contribution_candidates(
        results: Sequence[ContributedResult], *, members: set[str]
    ) -> list[str]:
        """List the canonical member URLs a batch could contribute to.

        No other URL's state can affect the batch, so scoping the state
        read to these keeps it bounded by the batch rather than by the
        number of URLs in the check.
        """
        candidates: set[str] = set()
        for result in results:
            if not is_supported_url(result.url):
                continue
            url = canonicalize_url(result.url)
            if url in members:
                candidates.add(url)
        return sorted(candidates)

    def _partition_contributions(
        self,
        results: Sequence[ContributedResult],
        *,
        members: set[str],
        states: Mapping[str, LinkState],
    ) -> tuple[list[ContributedResult], list[RejectedContribution]]:
        """Split contributed results into the eligible and the rejected.

        A result is eligible when its URL is checkable, is a member of the
        check, is in a state open to contribution (see
        `~ook.domain.linkcheck.accepts_contribution`), and has not already
        been contributed earlier in the same batch. Eligible results come
        back canonicalized, so downstream lookups and writes key on the
        same URL the check's membership does.

        Eligibility is judged from ``states``, the same rows that become
        each accepted result's prior state, so the judgement and the write
        it authorizes see one snapshot. A member URL absent from
        ``states`` has never been checked, which is pending: Ook has no
        verdict there to be unable to reach.
        """
        eligible: list[ContributedResult] = []
        seen: set[str] = set()
        rejected: list[RejectedContribution] = []

        def reject(
            url: str, reason: ContributionRejectionReason, message: str
        ) -> None:
            rejected.append(
                RejectedContribution(url=url, reason=reason, message=message)
            )

        for result in results:
            if not is_supported_url(result.url):
                reject(
                    result.url,
                    ContributionRejectionReason.unsupported_url,
                    "The URL is not a checkable http(s) URL, so it cannot"
                    " be a member of this check.",
                )
                continue
            url = canonicalize_url(result.url)
            state = states.get(url)
            if url not in members:
                reject(
                    result.url,
                    ContributionRejectionReason.not_a_member,
                    "The URL is not one of this check's submitted URLs.",
                )
            elif not accepts_contribution(state):
                reject(
                    result.url,
                    ContributionRejectionReason.not_blocked,
                    self._ineligible_message(state),
                )
            elif url in seen:
                reject(
                    result.url,
                    ContributionRejectionReason.duplicate,
                    "An earlier result in this batch already contributed"
                    " a result for this URL.",
                )
            else:
                seen.add(url)
                eligible.append(result.model_copy(update={"url": url}))
        return eligible, rejected

    @staticmethod
    def _ineligible_message(state: LinkState | None) -> str:
        """Explain why a URL's stored state accepts no contributed result.

        The reason enum is one value for every ineligible state, so the
        message carries the distinguishing detail: the state's own status,
        the HTTP status code behind it when Ook got a response, and the
        two states that are eligible.
        """
        if state is None:
            # No state row at all: never checked, which is pending.
            status = CheckUrlStatus.pending.value
            detail = ""
        else:
            status = state.status.value
            detail = (
                ""
                if state.status_code is None
                else f" (HTTP {state.status_code})"
            )
        return (
            f"The URL is {status}{detail}. Contributed results apply only"
            " to a URL Ook found blocked, or found broken with no terminal"
            " HTTP status at all: those are the verdicts Ook could not"
            " reach from its own vantage point."
        )

    async def list_due_recheck_urls(self) -> list[DueUrl]:
        """Enumerate due, still-referenced URLs for scheduled recheck.

        Returns
        -------
        list of DueUrl
            The URLs due for a (re)check that still occur on at least
            one origin page, never-checked first, then oldest last
            check first. These are the URLs the scheduled recheck
            enqueues as batched Kafka messages.
        """
        now = datetime.now(tz=UTC)
        return await self._store.get_due_urls(
            now=now, ttl=self._freshness_ttl, referenced_only=True
        )

    async def purge_expired_records(self) -> PurgeResult:
        """Purge expired check records and orphaned URL records.

        Checks older than the retention period are deleted first, then
        URL records with no remaining origin-page occurrences and no
        membership in a retained check.

        Returns
        -------
        PurgeResult
            The counts of purged checks and URL records.
        """
        now = datetime.now(tz=UTC)
        check_count = await self._store.purge_expired_checks(
            now=now, retention=self._check_retention
        )
        url_count = await self._store.purge_orphan_urls()
        self._logger.info(
            "Purged expired link-check records",
            check_count=check_count,
            url_count=url_count,
        )
        return PurgeResult(check_count=check_count, url_count=url_count)

    async def get_check_report(self, check_id: int) -> LinkCheckReport | None:
        """Get the status report for a submitted check.

        Parameters
        ----------
        check_id
            The check's identifier.

        Returns
        -------
        LinkCheckReport or None
            The check's report with per-URL results, or None if the
            check is unknown.
        """
        record = await self._store.get_check(check_id)
        if record is None:
            return None
        return LinkCheckReport(
            id=record.id,
            origin_base_url=record.origin_base_url,
            is_default_version=record.is_default_version,
            status=record.status,
            date_created=record.date_created,
            date_completed=record.date_completed,
            urls=[
                self._report_url(url_record, record.date_created)
                for url_record in record.urls
            ],
        )

    async def get_url_record(self, url: str) -> UrlRecord | None:
        """Get a URL's stored health record.

        Parameters
        ----------
        url
            The URL to look up. It is canonicalized (fragment stripped)
            before the lookup.

        Returns
        -------
        UrlRecord or None
            The URL's record with its origin-page occurrences, or None
            if the URL is unknown.
        """
        return await self._store.get_url_record(canonicalize_url(url))

    async def get_origin_links(
        self,
        origin_base_url: str,
        *,
        status: CheckUrlStatus | None = None,
        path: str | None = None,
        cursor: OriginLinksCursor | None = None,
        limit: int | None = None,
    ) -> CountedPaginatedList[OriginLink, OriginLinksCursor]:
        """Get an origin's links with their health states, paginated.

        Parameters
        ----------
        origin_base_url
            The normalized base URL of the origin whose links are
            listed.
        status
            If given, only links with this status are listed. The
            ``redirected`` status lists links whose sources should be
            updated to their new locations; ``broken`` is the
            rot-monitoring view.
        path
            If given, only links that occur on this page path are
            listed; each listed link still carries its full set of
            origin page paths.
        cursor
            The pagination cursor for the query.
        limit
            The maximum number of links to return, or None for all.

        Returns
        -------
        CountedPaginatedList
            A paginated list of the origin's links, ordered by URL.
        """
        return await self._store.get_origin_links(
            origin_base_url,
            status=status,
            path=path,
            cursor=cursor,
            limit=limit,
        )

    def _is_due(self, state: LinkState | None, now: datetime) -> bool:
        """Determine whether a URL needs a (re)check at submission.

        Mirrors the store's due-URL enumeration rule: never-checked,
        ladder-recheck-time arrived, or stale beyond the freshness TTL.
        """
        if state is None:
            return True
        if state.status is LinkStatus.unsupported:
            return False
        if state.date_next_check is not None and state.date_next_check <= now:
            return True
        return state.date_checked <= now - self._freshness_ttl

    def _report_url(
        self, url_record: CheckUrlRecord, submitted_at: datetime
    ) -> CheckedUrlReport:
        """Build the per-URL report entry for a check member.

        A URL's stored result counts for the check when it is fresh
        relative to the submission time; otherwise the URL is pending
        until execution refreshes it.
        """
        status = url_record.status
        date_checked = url_record.date_last_checked
        if (
            status is None
            or date_checked is None
            or date_checked < submitted_at - self._freshness_ttl
        ):
            return CheckedUrlReport(
                url=url_record.url,
                status=CheckUrlStatus.pending,
                origin_paths=url_record.origin_paths,
            )
        return CheckedUrlReport(
            url=url_record.url,
            status=CheckUrlStatus.from_link_status(status),
            status_code=url_record.status_code,
            redirect_status_code=url_record.redirect_status_code,
            redirect_url=url_record.redirect_url,
            error=url_record.error,
            date_checked=date_checked,
            origin_paths=url_record.origin_paths,
            result_source=url_record.result_source,
            contributed_by=url_record.contributed_by,
        )
