"""Storage interface for link-check records."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.stdlib import BoundLogger

from ook.dbschema.linkcheck import (
    SqlCheckedUrl,
    SqlLinkCheck,
    SqlLinkCheckUrl,
    SqlUrlOccurrence,
)
from ook.domain.linkcheck import (
    CheckRunStatus,
    LinkState,
    LinkStatus,
    UrlOccurrence,
)

from ._query import (
    create_check_urls_stmt,
    create_checked_url_ids_stmt,
    create_due_urls_stmt,
    create_url_states_stmt,
)

__all__ = ["CheckRecord", "CheckUrlRecord", "DueUrl", "LinkCheckStore"]


@dataclass(frozen=True, slots=True)
class DueUrl:
    """A checked URL that is due for a (re)check."""

    id: int
    """The ``checked_url`` primary key."""

    url: str
    """The canonical (fragment-stripped) URL."""


@dataclass(frozen=True, slots=True)
class CheckUrlRecord:
    """The stored state of a URL that is a member of a link check."""

    url: str
    """The canonical (fragment-stripped) URL."""

    status: LinkStatus | None
    """The URL's health status, or None if it has never been checked."""

    last_checked_at: datetime | None
    """The time of the most recent check, or None if never checked."""

    status_code: int | None
    """The final HTTP status code from the most recent check."""

    redirect_status_code: int | None
    """The redirect's HTTP status code, if the URL redirected."""

    redirect_url: str | None
    """The final resolved location, if the URL redirected."""

    error: str | None
    """A description of the failure from the most recent check."""


@dataclass(frozen=True, slots=True)
class CheckRecord:
    """A stored link check with its member URL states."""

    id: int
    """The ``linkcheck_check`` primary key."""

    ltd_slug: str
    """The LTD project slug the check was submitted for."""

    default_branch: bool
    """Whether the submission is a default-branch build."""

    status: CheckRunStatus
    """The processing status of the check."""

    date_created: datetime
    """The time the check was submitted."""

    date_completed: datetime | None
    """The time the check completed, or None while unfinished."""

    urls: list[CheckUrlRecord]
    """The check's member URL states, ordered by URL."""


class LinkCheckStore:
    """Interface for storing link-check records in a database."""

    def __init__(self, session: AsyncSession, logger: BoundLogger) -> None:
        self._session = session
        self._logger = logger

    async def upsert_checked_urls(
        self, urls: Sequence[str], *, now: datetime | None = None
    ) -> dict[str, int]:
        """Ensure checked-URL records exist for the given canonical URLs.

        URLs that already have records are left untouched; new URLs get
        records in the never-checked state (null status and timestamps).

        Parameters
        ----------
        urls
            Canonical (fragment-stripped) URLs.
        now
            The creation time recorded for new rows. Defaults to the
            current time.

        Returns
        -------
        dict
            A mapping of each URL to its ``checked_url`` primary key.
        """
        unique_urls = list(dict.fromkeys(urls))
        if not unique_urls:
            return {}
        if now is None:
            now = datetime.now(tz=UTC)

        insert_stmt = pg_insert(SqlCheckedUrl).values(
            [{"url": url, "date_created": now} for url in unique_urls]
        )
        await self._session.execute(
            insert_stmt.on_conflict_do_nothing(index_elements=["url"])
        )

        result = await self._session.execute(
            create_checked_url_ids_stmt(unique_urls)
        )
        return {row.url: row.id for row in result.all()}

    async def upsert_url_state(
        self, state: LinkState, *, now: datetime | None = None
    ) -> None:
        """Write a URL's check state, creating its record if needed.

        Parameters
        ----------
        state
            The URL's state after a check, as produced by the
            status-transition engine.
        now
            The creation time recorded if a new row is inserted.
            Defaults to the current time.
        """
        if now is None:
            now = datetime.now(tz=UTC)

        state_columns = {
            "status": state.status.value,
            "last_checked_at": state.checked_at,
            "last_ok_at": state.last_ok_at,
            "failing_since": state.failing_since,
            "failure_count": state.failure_count,
            "status_code": state.status_code,
            "redirect_status_code": state.redirect_status_code,
            "redirect_url": state.redirect_url,
            "error": state.error,
            "next_check_at": state.next_check_at,
        }
        insert_stmt = pg_insert(SqlCheckedUrl).values(
            url=state.url, date_created=now, **state_columns
        )
        await self._session.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=["url"], set_=state_columns
            )
        )
        await self._session.flush()

    async def get_url_state(self, url: str) -> LinkState | None:
        """Get a URL's check state.

        Parameters
        ----------
        url
            The canonical (fragment-stripped) URL to look up.

        Returns
        -------
        LinkState or None
            The URL's state, or None if the URL is unknown or has never
            been checked (a record created at submission that has no
            check results yet).
        """
        states = await self.get_url_states([url])
        return states.get(url)

    async def get_url_states(
        self, urls: Sequence[str]
    ) -> dict[str, LinkState]:
        """Get the check states of URLs in bulk.

        Parameters
        ----------
        urls
            The canonical (fragment-stripped) URLs to look up.

        Returns
        -------
        dict
            A mapping of URL to state. URLs that are unknown or have
            never been checked are omitted.
        """
        if not urls:
            return {}
        result = await self._session.execute(create_url_states_stmt(urls))
        return {
            row.url: LinkState.model_validate(row, from_attributes=True)
            for row in result.all()
            if row.status is not None
        }

    async def create_check(
        self,
        *,
        ltd_slug: str,
        default_branch: bool,
        checked_url_ids: Sequence[int],
        now: datetime | None = None,
    ) -> int:
        """Create a link check with its URL membership.

        The check is created in the ``pending`` status.

        Parameters
        ----------
        ltd_slug
            The LTD project slug the check is submitted for.
        default_branch
            Whether the submission is a default-branch build.
        checked_url_ids
            Primary keys of the ``checked_url`` records that are members
            of this check (from `upsert_checked_urls`).
        now
            The submission time recorded for the check. Defaults to the
            current time.

        Returns
        -------
        int
            The primary key of the created check.
        """
        if now is None:
            now = datetime.now(tz=UTC)

        check_id = (
            await self._session.execute(
                pg_insert(SqlLinkCheck)
                .values(
                    ltd_slug=ltd_slug,
                    default_branch=default_branch,
                    status=CheckRunStatus.pending.value,
                    date_created=now,
                )
                .returning(SqlLinkCheck.id)
            )
        ).scalar_one()

        unique_url_ids = list(dict.fromkeys(checked_url_ids))
        if unique_url_ids:
            await self._session.execute(
                pg_insert(SqlLinkCheckUrl).values(
                    [
                        {"check_id": check_id, "checked_url_id": url_id}
                        for url_id in unique_url_ids
                    ]
                )
            )
        await self._session.flush()

        self._logger.debug(
            "Created link check",
            check_id=check_id,
            ltd_slug=ltd_slug,
            url_count=len(unique_url_ids),
        )
        return check_id

    async def update_check_status(
        self,
        check_id: int,
        status: CheckRunStatus,
        *,
        now: datetime | None = None,
    ) -> None:
        """Advance a check's processing status.

        Parameters
        ----------
        check_id
            The primary key of the check.
        status
            The new processing status. Advancing to
            `~ook.domain.linkcheck.CheckRunStatus.complete` also records
            the completion time.
        now
            The completion time recorded when the status is ``complete``.
            Defaults to the current time.
        """
        values: dict[str, object] = {"status": status.value}
        if status is CheckRunStatus.complete:
            values["date_completed"] = now or datetime.now(tz=UTC)
        await self._session.execute(
            update(SqlLinkCheck)
            .where(SqlLinkCheck.id == check_id)
            .values(**values)
        )
        await self._session.flush()

    async def get_check(self, check_id: int) -> CheckRecord | None:
        """Get a check with its member URL states.

        Parameters
        ----------
        check_id
            The primary key of the check.

        Returns
        -------
        CheckRecord or None
            The check with its member URL states ordered by URL, or None
            if the check is unknown.
        """
        check_row = (
            await self._session.execute(
                select(
                    SqlLinkCheck.id,
                    SqlLinkCheck.ltd_slug,
                    SqlLinkCheck.default_branch,
                    SqlLinkCheck.status,
                    SqlLinkCheck.date_created,
                    SqlLinkCheck.date_completed,
                ).where(SqlLinkCheck.id == check_id)
            )
        ).first()
        if check_row is None:
            return None

        url_rows = (
            await self._session.execute(create_check_urls_stmt(check_id))
        ).all()
        return CheckRecord(
            id=check_row.id,
            ltd_slug=check_row.ltd_slug,
            default_branch=check_row.default_branch,
            status=CheckRunStatus(check_row.status),
            date_created=check_row.date_created,
            date_completed=check_row.date_completed,
            urls=[
                CheckUrlRecord(
                    url=row.url,
                    status=(
                        LinkStatus(row.status)
                        if row.status is not None
                        else None
                    ),
                    last_checked_at=row.last_checked_at,
                    status_code=row.status_code,
                    redirect_status_code=row.redirect_status_code,
                    redirect_url=row.redirect_url,
                    error=row.error,
                )
                for row in url_rows
            ],
        )

    async def replace_project_occurrences(
        self,
        *,
        ltd_slug: str,
        occurrences: Sequence[UrlOccurrence],
        now: datetime | None = None,
    ) -> None:
        """Replace a project's URL occurrence set.

        The project's existing occurrences are deleted and replaced with
        the given set (this happens when a default-branch submission
        arrives). Checked-URL records are created for any URLs that
        don't have them yet.

        Parameters
        ----------
        ltd_slug
            The LTD project slug whose occurrence set is replaced.
        occurrences
            The project's new occurrence set. Duplicate occurrences
            collapse to a single row.
        now
            The creation time recorded for new checked-URL rows.
            Defaults to the current time.
        """
        url_ids = await self.upsert_checked_urls(
            [occurrence.url for occurrence in occurrences], now=now
        )

        await self._session.execute(
            delete(SqlUrlOccurrence).where(
                SqlUrlOccurrence.ltd_slug == ltd_slug
            )
        )

        occurrence_rows = list(
            dict.fromkeys(
                (url_ids[occurrence.url], occurrence.path)
                for occurrence in occurrences
            )
        )
        if occurrence_rows:
            await self._session.execute(
                pg_insert(SqlUrlOccurrence).values(
                    [
                        {
                            "ltd_slug": ltd_slug,
                            "checked_url_id": checked_url_id,
                            "path": path,
                        }
                        for checked_url_id, path in occurrence_rows
                    ]
                )
            )
        await self._session.flush()

        self._logger.debug(
            "Replaced project URL occurrences",
            ltd_slug=ltd_slug,
            occurrence_count=len(occurrence_rows),
        )

    async def get_due_urls(
        self,
        *,
        now: datetime,
        ttl: timedelta,
        limit: int | None = None,
    ) -> list[DueUrl]:
        """Enumerate URLs that are due for a (re)check.

        A URL is due when it has never been checked, when its
        retry-ladder recheck time has arrived, or when its last check is
        older than the freshness TTL. Unsupported URLs are never due.

        Parameters
        ----------
        now
            The current time.
        ttl
            The freshness TTL, bound to application configuration by the
            service layer.
        limit
            The maximum number of URLs to return, or None for no limit.

        Returns
        -------
        list of DueUrl
            The due URLs, never-checked first, then oldest last check
            first.
        """
        result = await self._session.execute(
            create_due_urls_stmt(now=now, ttl=ttl, limit=limit)
        )
        return [DueUrl(id=row.id, url=row.url) for row in result.all()]
