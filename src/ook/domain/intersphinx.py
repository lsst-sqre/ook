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
