"""Query builders for the link-check store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Select, func, or_, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import aliased

from ook.dbschema.linkcheck import (
    SqlCheckedUrl,
    SqlLinkCheckUrl,
    SqlUrlOccurrence,
)
from ook.domain.linkcheck import CheckUrlStatus, LinkStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta

__all__ = [
    "create_check_urls_stmt",
    "create_checked_url_ids_stmt",
    "create_due_urls_stmt",
    "create_origin_links_stmt",
    "create_url_occurrences_stmt",
    "create_url_record_stmt",
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
        SqlCheckedUrl.consecutive_blocked_count,
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
            SqlLinkCheckUrl.origin_paths,
        )
        .join(
            SqlLinkCheckUrl,
            SqlLinkCheckUrl.checked_url_id == SqlCheckedUrl.id,
        )
        .where(SqlLinkCheckUrl.check_id == check_id)
        .order_by(SqlCheckedUrl.url.asc())
    )


def create_url_record_stmt(url: str) -> Select:
    """Create a select statement for a URL's full stored record.

    Parameters
    ----------
    url
        The canonical (fragment-stripped) URL to look up.

    Returns
    -------
    Select
        A statement selecting the URL's state and bookkeeping columns.
    """
    return select(
        SqlCheckedUrl.url,
        SqlCheckedUrl.status,
        SqlCheckedUrl.status_code,
        SqlCheckedUrl.redirect_status_code,
        SqlCheckedUrl.redirect_url,
        SqlCheckedUrl.error,
        SqlCheckedUrl.last_checked_at,
        SqlCheckedUrl.last_ok_at,
        SqlCheckedUrl.failing_since,
        SqlCheckedUrl.failure_count,
        SqlCheckedUrl.next_check_at,
        SqlCheckedUrl.date_created,
    ).where(SqlCheckedUrl.url == url)


def create_url_occurrences_stmt(url: str) -> Select:
    """Create a select statement for a URL's origin-page occurrences.

    Parameters
    ----------
    url
        The canonical (fragment-stripped) URL to look up.

    Returns
    -------
    Select
        A statement selecting ``(origin_base_url, origin_path)`` rows,
        ordered by origin base URL and page path.
    """
    return (
        select(SqlUrlOccurrence.origin_base_url, SqlUrlOccurrence.origin_path)
        .join(
            SqlCheckedUrl,
            SqlCheckedUrl.id == SqlUrlOccurrence.checked_url_id,
        )
        .where(SqlCheckedUrl.url == url)
        .order_by(
            SqlUrlOccurrence.origin_base_url.asc(),
            SqlUrlOccurrence.origin_path.asc(),
        )
    )


def create_origin_links_stmt(
    origin_base_url: str,
    *,
    status: CheckUrlStatus | None = None,
    path: str | None = None,
) -> Select:
    """Create a select statement for an origin's links with their
    health states.

    Each row is one canonical URL occurring on the origin website, with
    its state columns and the aggregated page paths where it occurs.
    The columns are labelled to match the field names of the
    `ook.domain.linkcheck.OriginLink` domain model; never-checked URLs
    report the ``pending`` status.

    Parameters
    ----------
    origin_base_url
        The normalized base URL of the origin whose links are listed.
    status
        If given, only links with this status are selected. The
        ``pending`` status selects never-checked links.
    path
        If given, only links that occur on this page path (within the
        origin) are selected. This only narrows which links are listed:
        each selected link's aggregated ``origin_paths`` still lists
        every page it occurs on.

    Returns
    -------
    Select
        A statement selecting the origin's links, suitable for
        pagination.
    """
    stmt = (
        select(
            SqlCheckedUrl.url,
            func.coalesce(
                SqlCheckedUrl.status, CheckUrlStatus.pending.value
            ).label("status"),
            SqlCheckedUrl.status_code,
            SqlCheckedUrl.redirect_status_code,
            SqlCheckedUrl.redirect_url,
            SqlCheckedUrl.error,
            SqlCheckedUrl.last_checked_at.label("checked_at"),
            func.array_agg(
                aggregate_order_by(
                    SqlUrlOccurrence.origin_path,
                    SqlUrlOccurrence.origin_path.asc(),
                )
            ).label("origin_paths"),
        )
        .join(
            SqlUrlOccurrence,
            SqlUrlOccurrence.checked_url_id == SqlCheckedUrl.id,
        )
        .where(SqlUrlOccurrence.origin_base_url == origin_base_url)
        .group_by(SqlCheckedUrl.id)
    )
    if status is CheckUrlStatus.pending:
        stmt = stmt.where(SqlCheckedUrl.status.is_(None))
    elif status is not None:
        stmt = stmt.where(SqlCheckedUrl.status == status.value)
    if path is not None:
        # Restrict to links occurring on this page via a correlated
        # EXISTS over an aliased occurrence table, so the outer join's
        # ``origin_paths`` aggregation still spans every page the link
        # occurs on rather than collapsing to the filtered path.
        occurrence = aliased(SqlUrlOccurrence)
        stmt = stmt.where(
            select(occurrence.id)
            .where(
                occurrence.checked_url_id == SqlCheckedUrl.id,
                occurrence.origin_base_url == origin_base_url,
                occurrence.origin_path == path,
            )
            .exists()
        )
    return stmt


def create_due_urls_stmt(
    *,
    now: datetime,
    ttl: timedelta,
    limit: int | None = None,
    referenced_only: bool = False,
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
    referenced_only
        If true, only URLs that still occur on at least one origin
        page are selected.

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
    if referenced_only:
        stmt = stmt.where(
            select(SqlUrlOccurrence.id)
            .where(SqlUrlOccurrence.checked_url_id == SqlCheckedUrl.id)
            .exists()
        )
    if limit is not None:
        stmt = stmt.limit(limit)
    return stmt
