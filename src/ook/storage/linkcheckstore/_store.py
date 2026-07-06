"""Storage interface for link-check records."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Self, cast, override

from safir.database import (
    CountedPaginatedList,
    CountedPaginatedQueryRunner,
    PaginationCursor,
)
from sqlalchemy import CursorResult, Select, delete, select, update
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
    CheckUrlStatus,
    LinkState,
    LinkStatus,
    OriginLink,
    OriginPage,
    UrlOccurrence,
    UrlRecord,
)

from ._query import (
    create_check_urls_stmt,
    create_checked_url_ids_stmt,
    create_due_urls_stmt,
    create_origin_links_stmt,
    create_url_occurrences_stmt,
    create_url_record_stmt,
    create_url_states_stmt,
)

__all__ = [
    "CheckRecord",
    "CheckUrlRecord",
    "DueUrl",
    "LinkCheckStore",
    "OriginLinksCursor",
]


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

    origin_base_url: str
    """The normalized base URL of the origin website the check was
    submitted for.
    """

    is_default_version: bool
    """Whether the submission is a build of the origin's default
    version.
    """

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

    async def get_url_record(self, url: str) -> UrlRecord | None:
        """Get a URL's full stored record with its occurrences.

        Parameters
        ----------
        url
            The canonical (fragment-stripped) URL to look up.

        Returns
        -------
        UrlRecord or None
            The URL's record with its origin-page occurrences ordered
            by origin base URL and page path, or None if the URL is
            unknown. Never-checked URLs are reported with the
            ``pending`` status.
        """
        row = (
            await self._session.execute(create_url_record_stmt(url))
        ).first()
        if row is None:
            return None

        occurrence_rows = (
            await self._session.execute(create_url_occurrences_stmt(url))
        ).all()
        return UrlRecord(
            url=row.url,
            status=(
                CheckUrlStatus(row.status)
                if row.status is not None
                else CheckUrlStatus.pending
            ),
            status_code=row.status_code,
            redirect_status_code=row.redirect_status_code,
            redirect_url=row.redirect_url,
            error=row.error,
            last_checked_at=row.last_checked_at,
            last_ok_at=row.last_ok_at,
            failing_since=row.failing_since,
            failure_count=row.failure_count,
            next_check_at=row.next_check_at,
            date_created=row.date_created,
            occurrences=[
                OriginPage(
                    origin_base_url=occ.origin_base_url,
                    origin_path=occ.origin_path,
                )
                for occ in occurrence_rows
            ],
        )

    async def get_origin_links(
        self,
        origin_base_url: str,
        *,
        status: CheckUrlStatus | None = None,
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
            ``pending`` status lists never-checked links.
        cursor
            The pagination cursor for the query.
        limit
            The maximum number of links to return, or None for all.

        Returns
        -------
        CountedPaginatedList
            A paginated list of the origin's links, ordered by URL.
        """
        runner = CountedPaginatedQueryRunner(
            entry_type=OriginLink,
            cursor_type=OriginLinksCursor,
        )
        return await runner.query_row(
            session=self._session,
            stmt=create_origin_links_stmt(origin_base_url, status=status),
            cursor=cursor,
            limit=limit,
        )

    async def create_check(
        self,
        *,
        origin_base_url: str,
        is_default_version: bool,
        checked_url_ids: Sequence[int],
        now: datetime | None = None,
    ) -> int:
        """Create a link check with its URL membership.

        The check is created in the ``pending`` status.

        Parameters
        ----------
        origin_base_url
            The normalized base URL of the origin website the check is
            submitted for.
        is_default_version
            Whether the submission is a build of the origin's default
            version.
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
                    origin_base_url=origin_base_url,
                    is_default_version=is_default_version,
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
            origin_base_url=origin_base_url,
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
                    SqlLinkCheck.origin_base_url,
                    SqlLinkCheck.is_default_version,
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
            origin_base_url=check_row.origin_base_url,
            is_default_version=check_row.is_default_version,
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

    async def replace_origin_occurrences(
        self,
        *,
        origin_base_url: str,
        occurrences: Sequence[UrlOccurrence],
        now: datetime | None = None,
    ) -> None:
        """Replace an origin's URL occurrence set.

        The origin's existing occurrences are deleted and replaced with
        the given set (this happens when a default-version submission
        arrives). Checked-URL records are created for any URLs that
        don't have them yet.

        Parameters
        ----------
        origin_base_url
            The normalized base URL of the origin whose occurrence set
            is replaced.
        occurrences
            The origin's new occurrence set. Duplicate occurrences
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
                SqlUrlOccurrence.origin_base_url == origin_base_url
            )
        )

        occurrence_rows = list(
            dict.fromkeys(
                (url_ids[occurrence.url], occurrence.origin_path)
                for occurrence in occurrences
            )
        )
        if occurrence_rows:
            await self._session.execute(
                pg_insert(SqlUrlOccurrence).values(
                    [
                        {
                            "origin_base_url": origin_base_url,
                            "checked_url_id": checked_url_id,
                            "origin_path": origin_path,
                        }
                        for checked_url_id, origin_path in occurrence_rows
                    ]
                )
            )
        await self._session.flush()

        self._logger.debug(
            "Replaced origin URL occurrences",
            origin_base_url=origin_base_url,
            occurrence_count=len(occurrence_rows),
        )

    async def get_due_urls(
        self,
        *,
        now: datetime,
        ttl: timedelta,
        limit: int | None = None,
        referenced_only: bool = False,
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
        referenced_only
            If true, only URLs that still occur on at least one origin
            page are enumerated. The scheduled recheck uses this so
            URLs that no longer appear in any documentation build are
            not rechecked.

        Returns
        -------
        list of DueUrl
            The due URLs, never-checked first, then oldest last check
            first.
        """
        result = await self._session.execute(
            create_due_urls_stmt(
                now=now, ttl=ttl, limit=limit, referenced_only=referenced_only
            )
        )
        return [DueUrl(id=row.id, url=row.url) for row in result.all()]

    async def get_urls_by_ids(self, ids: Sequence[int]) -> list[DueUrl]:
        """Look up checked URLs by their primary keys.

        Parameters
        ----------
        ids
            The ``checked_url`` primary keys to look up.

        Returns
        -------
        list of DueUrl
            The known ``(id, url)`` pairs ordered by id. Unknown ids
            are omitted: recheck requests are delivered at least once
            and may outlive their URL records.
        """
        unique_ids = list(dict.fromkeys(ids))
        if not unique_ids:
            return []
        result = await self._session.execute(
            select(SqlCheckedUrl.id, SqlCheckedUrl.url)
            .where(SqlCheckedUrl.id.in_(unique_ids))
            .order_by(SqlCheckedUrl.id.asc())
        )
        return [DueUrl(id=row.id, url=row.url) for row in result.all()]

    async def purge_expired_checks(
        self, *, now: datetime, retention: timedelta
    ) -> int:
        """Delete check records older than the retention period.

        A check's URL-membership rows are deleted with it (the database
        cascades the foreign key); the checked-URL records themselves
        are left in place.

        Parameters
        ----------
        now
            The current time.
        retention
            The retention period: checks submitted earlier than
            ``now - retention`` are deleted.

        Returns
        -------
        int
            The number of deleted checks.
        """
        result = await self._session.execute(
            delete(SqlLinkCheck).where(
                SqlLinkCheck.date_created < now - retention
            )
        )
        await self._session.flush()
        count = cast("CursorResult", result).rowcount
        self._logger.debug("Purged expired link checks", check_count=count)
        return count

    async def purge_orphan_urls(self) -> int:
        """Delete checked-URL records that are no longer referenced.

        A URL record is orphaned when it has no remaining origin-page
        occurrences and is not a member of any retained check. URLs
        that are only members of retained checks are kept so active
        check reports never lose members; they become orphans once
        `purge_expired_checks` removes those checks.

        Returns
        -------
        int
            The number of deleted URL records.
        """
        has_occurrence = (
            select(SqlUrlOccurrence.id)
            .where(SqlUrlOccurrence.checked_url_id == SqlCheckedUrl.id)
            .exists()
        )
        has_membership = (
            select(SqlLinkCheckUrl.check_id)
            .where(SqlLinkCheckUrl.checked_url_id == SqlCheckedUrl.id)
            .exists()
        )
        result = await self._session.execute(
            delete(SqlCheckedUrl).where(~has_occurrence, ~has_membership)
        )
        await self._session.flush()
        count = cast("CursorResult", result).rowcount
        self._logger.debug("Purged orphaned checked URLs", url_count=count)
        return count


@dataclass(slots=True)
class OriginLinksCursor(PaginationCursor[OriginLink]):
    """Cursor for paginating an origin's links, sorted by URL."""

    url: str
    """The canonical URL of the link."""

    @override
    @classmethod
    def from_entry(cls, entry: OriginLink, *, reverse: bool = False) -> Self:
        """Create a cursor from an origin-link entry as the bound.

        Parameters
        ----------
        entry
            The origin-link entry.
        reverse
            Whether the cursor is for the previous page.

        Returns
        -------
        OriginLinksCursor
            The cursor object.
        """
        return cls(url=entry.url, previous=reverse)

    @override
    @classmethod
    def from_str(cls, cursor: str) -> Self:
        """Create a cursor from a string.

        Parameters
        ----------
        cursor
            The cursor string.

        Returns
        -------
        OriginLinksCursor
            The cursor object.
        """
        previous_prefix = "p__"
        if cursor.startswith(previous_prefix):
            url = cursor.removeprefix(previous_prefix)
            previous = True
        else:
            url = cursor
            previous = False
        return cls(url=url, previous=previous)

    @override
    @classmethod
    def apply_order(cls, stmt: Select, *, reverse: bool = False) -> Select:
        """Apply the sort order of the cursor to a select statement.

        Parameters
        ----------
        stmt
            The SQLAlchemy statement to apply ordering to.
        reverse
            Whether the ordering should be reversed.

        Returns
        -------
        Select
            The modified SQLAlchemy statement with ordering applied.
        """
        return stmt.order_by(
            SqlCheckedUrl.url.desc() if reverse else SqlCheckedUrl.url.asc()
        )

    @override
    def apply_cursor(self, stmt: Select) -> Select:
        """Apply the cursor to a select statement.

        Parameters
        ----------
        stmt
            The SQLAlchemy statement to apply the cursor to.

        Returns
        -------
        Select
            The modified SQLAlchemy statement with the cursor applied.
        """
        if self.previous:
            return stmt.where(SqlCheckedUrl.url < self.url)

        # In the forward direction, include the cursor's own URL.
        return stmt.where(SqlCheckedUrl.url >= self.url)

    @override
    def invert(self) -> Self:
        """Invert the cursor.

        Returns
        -------
        OriginLinksCursor
            The inverted cursor.
        """
        return type(self)(url=self.url, previous=not self.previous)

    def __str__(self) -> str:
        """Convert the cursor to a string.

        Returns
        -------
        str
            The string representation of the cursor.
        """
        return f"p__{self.url}" if self.previous else self.url
