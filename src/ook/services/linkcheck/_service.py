"""Service for orchestrating submitted link checks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ook.domain.linkcheck import (
    CheckedUrlReport,
    CheckRunStatus,
    CheckUrlStatus,
    LinkCheckReport,
    LinkState,
    LinkStatus,
    ProjectLink,
    SubmittedUrl,
    UrlOccurrence,
    UrlRecord,
    canonicalize_url,
    evaluate_outcome,
    is_supported_url,
)
from ook.exceptions import LinkCheckTooManyUrlsError
from ook.storage.linkcheckstore import DueUrl

if TYPE_CHECKING:
    from collections.abc import Sequence

    from safir.database import CountedPaginatedList
    from structlog.stdlib import BoundLogger

    from ook.domain.linkcheck import RetryLadderConfig
    from ook.storage.linkcheckstore import (
        CheckUrlRecord,
        LinkCheckStore,
        ProjectLinksCursor,
    )

    from ._urlchecker import UrlChecker

__all__ = ["LinkCheckService", "SubmittedCheck"]


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
    ) -> None:
        self._store = linkcheck_store
        self._logger = logger
        self._freshness_ttl = freshness_ttl
        self._max_urls_per_check = max_urls_per_check
        self._url_checker = url_checker
        self._retry_ladder = retry_ladder

    async def submit_check(
        self,
        *,
        ltd_slug: str,
        default_branch: bool,
        urls: Sequence[SubmittedUrl],
    ) -> SubmittedCheck:
        """Accept a link-check submission.

        URLs are canonicalized (fragments stripped) and partitioned:
        unsupported URLs resolve immediately, URLs with a fresh cached
        result keep it, and the rest are due for a check. The check and
        its URL membership are persisted; for default-branch submissions
        the project's occurrence set is replaced.

        Parameters
        ----------
        ltd_slug
            The LTD project slug the check is submitted for.
        default_branch
            Whether the submission is a default-branch build. Only
            default-branch submissions replace the project's occurrence
            set; all submissions receive full results.
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
            for path in submitted.paths:
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
                        checked_at=now,
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
            ltd_slug=ltd_slug,
            default_branch=default_branch,
            checked_url_ids=[url_ids[url] for url in canonical_urls],
            now=now,
        )

        if default_branch:
            occurrences = [
                UrlOccurrence(url=url, path=path)
                for url, paths in merged_paths.items()
                for path in paths
            ]
            await self._store.replace_project_occurrences(
                ltd_slug=ltd_slug, occurrences=occurrences, now=now
            )

        self._logger.info(
            "Accepted link-check submission",
            check_id=check_id,
            ltd_slug=ltd_slug,
            default_branch=default_branch,
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
            ltd_slug=record.ltd_slug,
            default_branch=record.default_branch,
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
            The URL's record with its project-page occurrences, or None
            if the URL is unknown.
        """
        return await self._store.get_url_record(canonicalize_url(url))

    async def get_project_links(
        self,
        ltd_slug: str,
        *,
        status: CheckUrlStatus | None = None,
        cursor: ProjectLinksCursor | None = None,
        limit: int | None = None,
    ) -> CountedPaginatedList[ProjectLink, ProjectLinksCursor]:
        """Get a project's links with their health states, paginated.

        Parameters
        ----------
        ltd_slug
            The LTD project slug whose links are listed.
        status
            If given, only links with this status are listed. The
            ``redirected`` status lists links whose sources should be
            updated to their new locations; ``broken`` is the
            rot-monitoring view.
        cursor
            The pagination cursor for the query.
        limit
            The maximum number of links to return, or None for all.

        Returns
        -------
        CountedPaginatedList
            A paginated list of the project's links, ordered by URL.
        """
        return await self._store.get_project_links(
            ltd_slug, status=status, cursor=cursor, limit=limit
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
        if state.next_check_at is not None and state.next_check_at <= now:
            return True
        return state.checked_at <= now - self._freshness_ttl

    def _report_url(
        self, url_record: CheckUrlRecord, submitted_at: datetime
    ) -> CheckedUrlReport:
        """Build the per-URL report entry for a check member.

        A URL's stored result counts for the check when it is fresh
        relative to the submission time; otherwise the URL is pending
        until execution refreshes it.
        """
        status = url_record.status
        checked_at = url_record.last_checked_at
        if (
            status is None
            or checked_at is None
            or checked_at < submitted_at - self._freshness_ttl
        ):
            return CheckedUrlReport(
                url=url_record.url, status=CheckUrlStatus.pending
            )
        return CheckedUrlReport(
            url=url_record.url,
            status=CheckUrlStatus.from_link_status(status),
            status_code=url_record.status_code,
            redirect_status_code=url_record.redirect_status_code,
            redirect_url=url_record.redirect_url,
            error=url_record.error,
            checked_at=checked_at,
        )
