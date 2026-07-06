"""Storage interface for link-check records."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.stdlib import BoundLogger

from ook.dbschema.linkcheck import (
    SqlCheckedUrl,
    SqlLinkCheck,
    SqlLinkCheckUrl,
    SqlUrlOccurrence,
)
from ook.domain.linkcheck import LinkState, UrlOccurrence

from ._query import (
    create_checked_url_ids_stmt,
    create_due_urls_stmt,
    create_url_state_stmt,
)

__all__ = ["DueUrl", "LinkCheckStore"]


@dataclass(frozen=True, slots=True)
class DueUrl:
    """A checked URL that is due for a (re)check."""

    id: int
    """The ``checked_url`` primary key."""

    url: str
    """The canonical (fragment-stripped) URL."""


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
        result = (
            await self._session.execute(create_url_state_stmt(url))
        ).first()
        if result is None or result.status is None:
            return None
        return LinkState.model_validate(result, from_attributes=True)

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
                    status="pending",
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
