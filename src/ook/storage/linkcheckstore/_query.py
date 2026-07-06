"""Query builders for the link-check store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Select, or_, select

from ook.dbschema.linkcheck import SqlCheckedUrl, SqlLinkCheckUrl
from ook.domain.linkcheck import LinkStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta

__all__ = [
    "create_check_urls_stmt",
    "create_checked_url_ids_stmt",
    "create_due_urls_stmt",
    "create_url_states_stmt",
]


def create_checked_url_ids_stmt(urls: list[str]) -> Select:
    """Create a select statement mapping canonical URLs to their IDs.

    Parameters
    ----------
    urls
        The canonical URLs to look up.

    Returns
    -------
    Select
        A statement selecting ``(url, id)`` rows for the given URLs.
    """
    return select(SqlCheckedUrl.url, SqlCheckedUrl.id).where(
        SqlCheckedUrl.url.in_(urls)
    )


def create_url_states_stmt(urls: Sequence[str]) -> Select:
    """Create a select statement for the check states of URLs.

    The columns are labelled to match the field names of the
    `ook.domain.linkcheck.LinkState` domain model.

    Parameters
    ----------
    urls
        The canonical URLs to look up.

    Returns
    -------
    Select
        A statement selecting the URLs' state columns.
    """
    return select(
        SqlCheckedUrl.url,
        SqlCheckedUrl.status,
        SqlCheckedUrl.last_checked_at.label("checked_at"),
        SqlCheckedUrl.last_ok_at,
        SqlCheckedUrl.failing_since,
        SqlCheckedUrl.failure_count,
        SqlCheckedUrl.status_code,
        SqlCheckedUrl.redirect_status_code,
        SqlCheckedUrl.redirect_url,
        SqlCheckedUrl.error,
        SqlCheckedUrl.next_check_at,
    ).where(SqlCheckedUrl.url.in_(urls))


def create_check_urls_stmt(check_id: int) -> Select:
    """Create a select statement for a check's member URL states.

    Parameters
    ----------
    check_id
        The primary key of the check.

    Returns
    -------
    Select
        A statement selecting each member URL's state columns, ordered
        by URL.
    """
    return (
        select(
            SqlCheckedUrl.url,
            SqlCheckedUrl.status,
            SqlCheckedUrl.last_checked_at,
            SqlCheckedUrl.status_code,
            SqlCheckedUrl.redirect_status_code,
            SqlCheckedUrl.redirect_url,
            SqlCheckedUrl.error,
        )
        .join(
            SqlLinkCheckUrl,
            SqlLinkCheckUrl.checked_url_id == SqlCheckedUrl.id,
        )
        .where(SqlLinkCheckUrl.check_id == check_id)
        .order_by(SqlCheckedUrl.url.asc())
    )


def create_due_urls_stmt(
    *, now: datetime, ttl: timedelta, limit: int | None = None
) -> Select:
    """Create a select statement enumerating URLs due for a check.

    A URL is due when it has never been checked, when its retry-ladder
    recheck time has arrived, or when its last check is older than the
    freshness TTL. Unsupported URLs are never due: they only change
    status if the URL itself changes.

    Parameters
    ----------
    now
        The current time.
    ttl
        The freshness TTL: URLs last checked earlier than ``now - ttl``
        are due.
    limit
        The maximum number of URLs to return, or None for no limit.

    Returns
    -------
    Select
        A statement selecting ``(id, url)`` rows for due URLs, ordered
        with never-checked URLs first, then by oldest last check.
    """
    stmt = (
        select(SqlCheckedUrl.id, SqlCheckedUrl.url)
        .where(
            or_(
                SqlCheckedUrl.last_checked_at.is_(None),
                SqlCheckedUrl.next_check_at <= now,
                SqlCheckedUrl.last_checked_at <= now - ttl,
            ),
            SqlCheckedUrl.status.is_distinct_from(
                LinkStatus.unsupported.value
            ),
        )
        .order_by(
            SqlCheckedUrl.last_checked_at.asc().nulls_first(),
            SqlCheckedUrl.id.asc(),
        )
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return stmt
