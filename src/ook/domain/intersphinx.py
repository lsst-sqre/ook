"""Domain models for the intersphinx inventory cache."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

__all__ = [
    "IntersphinxInventory",
    "InventoryFetchStatus",
]


class InventoryFetchStatus(StrEnum):
    """The outcome of the most recent upstream fetch of an inventory."""

    success = "success"
    """The most recent fetch (or conditional revalidation) succeeded and
    the stored content is upstream-fresh.
    """

    failure = "failure"
    """The most recent fetch failed. A row in this state with no content
    is the negative cache; a row with content is a stale copy retained for
    availability.
    """


@dataclass(frozen=True, slots=True)
class IntersphinxInventory:
    """A cached Sphinx ``objects.inv`` inventory keyed by its origin URL."""

    url: str
    """The full origin ``objects.inv`` URL (the unique cache key)."""

    content: bytes | None
    """The cached inventory bytes, or None for a failure-only (negative
    cache) row.
    """

    content_type: str | None
    """The stored ``Content-Type`` of the inventory, if known."""

    etag: str | None
    """The upstream ``ETag`` from the last successful fetch, for
    conditional ``If-None-Match`` revalidation.
    """

    last_modified: str | None
    """The upstream ``Last-Modified`` header from the last successful
    fetch, for conditional ``If-Modified-Since`` revalidation.
    """

    date_fetched: datetime | None
    """The time of the last upstream fetch attempt (success or failure).

    This is the freshness anchor: TTL and negative-TTL windows are measured
    against it, and a ``304 Not Modified`` revalidation bumps it while
    keeping the stored content.
    """

    date_requested: datetime
    """The time of the most recent client request for this inventory.

    The refresh job only revalidates inventories requested within the
    active window; inventories outside it are skipped until a new request
    reactivates them.
    """

    last_fetch_status: InventoryFetchStatus | None
    """The outcome of the last upstream fetch attempt, or None if the row
    was created without a fetch attempt.
    """

    last_fetch_error: str | None
    """A description of the last fetch failure, or None if the last fetch
    succeeded.
    """

    date_refresh_failed: datetime | None
    """The time of the most recent failed proactive refresh, or None when
    the last completed fetch succeeded.

    This is the refresh job's backoff marker, and the only failure record
    that does not ride on ``date_fetched``: a refresh failure must leave the
    stored copy — content, validators, and freshness anchor alike —
    untouched, so it has nowhere else to record when it happened. The due
    list holds a row back for the same TTL after a failure, so a broken
    inventory is retried on the normal refresh cadence instead of on every
    run. A successful fetch clears it.
    """

    # The redirect-resolution fields sort last and default to None, which is
    # the genuine "this chain did not redirect" value: the common case does
    # not have to spell them, while every field above stays required so a
    # construction site cannot silently drop one.

    resolved_url: str | None = None
    """The terminal URL the last fetch's redirect chain ended at, or None
    when the chain did not redirect.

    Recorded so a redirected inventory's resolved location can be surfaced
    without re-contacting the origin.
    """

    resolved_redirect_permanent: bool | None = None
    """Whether the last fetch's redirect chain was entirely permanent.

    True when every hop was a 301 or 308, False when any hop was temporary,
    and None when the chain did not redirect. Only an all-permanent chain
    means the requested URL itself should be updated at its source.
    """
