"""Domain models for the intersphinx inventory cache."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

__all__ = [
    "IntersphinxInventory",
    "InventoryCacheStatus",
    "InventoryFetchStatus",
    "ServedInventory",
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


class InventoryCacheStatus(StrEnum):
    """How a served inventory response was obtained from the cache.

    These are the outcomes a client can actually be handed a body (or a
    ``304`` about one) under, and nothing else: the cache's other outcomes —
    a negatively-cached failure, a refresh whose chain had moved, a failed
    refresh — never reach a served response, so they are not members here
    and stay literal log markers.
    """

    hit = "hit"
    """Served from the cached copy, which was fetched within the freshness
    TTL.
    """

    stale = "stale"
    """Served from the cached copy, which was fetched longer ago than the
    freshness TTL.

    Still a cache serve, not an error: the copy is deliberately retained
    past its TTL for availability while the background refresh job
    revalidates it.
    """

    miss = "miss"
    """Not cached at the start of this request; the origin was fetched
    synchronously to answer it.
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
    """The time of the most recent failed *proactive refresh*, or None when
    no refresh failure is outstanding.

    This is the refresh job's backoff marker, and it records failures on the
    refresh path alone — it is not a general "the last fetch failed" flag,
    and None does not mean the last fetch succeeded. A request-path failure
    leaves it None even though it writes a ``failure`` status and a
    ``last_fetch_error``: that write dates itself with ``date_fetched``,
    which already holds the row out of the refresh due list for a TTL, so a
    negative-cache row has nothing to add here. A refresh failure is the one
    case with nowhere else to record when it happened, because it must leave
    the stored copy — content, validators, and freshness anchor alike —
    untouched. The due list holds a row back for the same TTL after a marked
    failure, so a broken inventory is retried on the normal refresh cadence
    instead of on every run. Any successful fetch, on either path, clears
    it.
    """

    # Defaulting convention for the fields below. A field defaults to None
    # only when None is an *observation this record makes about its own
    # fetch*: `resolved_url` and `resolved_redirect_permanent` are null
    # exactly when the chain did not redirect, so a construction site with
    # no chain to report is right to say nothing, and the common case does
    # not have to spell them. Every field above stays required — including
    # `date_refresh_failed`, whose None is not an observation but a decision
    # to *clear* state this fetch did not produce (the refresh job's
    # backoff), which each writer of a whole record has to make on purpose
    # rather than inherit from a default.

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


@dataclass(frozen=True, slots=True)
class ServedInventory:
    """A cached inventory together with how this request obtained it.

    The cache status is decided where the serve happens — the freshness
    comparison on the cache-hit path, the fetch on the cold-miss path — and
    carried out with the record, because it cannot be recovered from the
    record afterwards: a cold miss stamps the same just-now ``date_fetched``
    a fast hit has, and a stale serve is only distinguishable from either by
    a TTL the caller does not hold.
    """

    inventory: IntersphinxInventory
    """The cached inventory record to serve."""

    cache_status: InventoryCacheStatus
    """How this request obtained that record."""
