"""Service for the intersphinx inventory cache."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections import Counter
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
from typing import NoReturn

import httpx
from httpx import AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.stdlib import BoundLogger

from ook.domain.intersphinx import IntersphinxInventory, InventoryFetchStatus
from ook.domain.redirects import (
    MAX_REDIRECTS,
    REDIRECT_CODES,
    is_permanent_chain,
)
from ook.exceptions import InvalidInventoryUrlError, UpstreamInventoryError
from ook.storage.intersphinxstore import IntersphinxInventoryStore

__all__ = [
    "HostResolver",
    "IntersphinxCacheService",
    "IntersphinxRefreshSummary",
]


@dataclass(frozen=True, slots=True)
class IntersphinxRefreshSummary:
    """The outcome of a proactive intersphinx refresh run."""

    considered: int
    """The number of stale, still-active inventories the run examined."""

    refreshed: int
    """The number of inventories whose content was replaced by a 200."""

    revalidated: int
    """The number of inventories a 304 revalidated in place."""

    superseded: int
    """The number of outcomes dropped because the row changed under the run.

    A client cold miss can commit good content while this run's fetch of the
    same inventory is still in flight, and the refresh's outcome describes the
    copy it started from, not the one the miss stored. The write is guarded on
    the freshness anchor the due-list read saw and dropped when it moved, so
    these are counted apart from the refreshes and revalidations that landed.
    """

    failed: int
    """The number of inventories whose refresh failed (logged, skipped)."""

    unrecorded_failures: int
    """The number of those failures whose own bookkeeping write also failed.

    Recording a failure is itself a database write, and it races the same
    client cold miss the write is guarded against, so it can raise a
    serialization or deadlock error of its own. Such an inventory keeps its
    old ``date_fetched`` and never receives the ``date_refresh_failed``
    backoff marker, so it heads the next run's due list and fails again;
    counting it apart from `failed` is what lets the run report a broken
    bookkeeping path without treating it as a reason to abandon the batch.
    """


class _RefreshResult(Enum):
    """How one inventory's proactive refresh ended, once its write returned.

    A failure never reaches here — it raises out of `_refresh_one` for
    `refresh_inventories` to log, back off, and count on its own.
    """

    refreshed = auto()
    """A ``200`` replaced the stored content."""

    revalidated = auto()
    """A ``304`` revalidated the stored copy in place."""

    superseded = auto()
    """The write was dropped: the row changed since the due-list read."""


class _BackoffWrite(Enum):
    """How the attempt to record one inventory's refresh failure ended."""

    recorded = auto()
    """The failure detail and backoff marker were written and committed."""

    dropped = auto()
    """The guard skipped the write: the row changed since the due-list read.

    A deliberate no-op, not an error: a client cold miss refreshed the row
    while this run's fetch was in flight, so this run's failure describes a
    copy the row no longer holds.
    """

    failed = auto()
    """The write itself raised, so the row carries no backoff marker."""


HostResolver = Callable[[str], Awaitable[Sequence[str]]]
"""Type of a callable resolving a hostname to IP address strings."""


_DEFAULT_MAX_CONTENT_SIZE = 50 * 1024 * 1024
"""Default cap, in bytes, on an origin inventory response body (50 MB)."""


_IF_NONE_MATCH = "If-None-Match"
"""The ``ETag`` half of the conditional request a revalidation sends."""


_IF_MODIFIED_SINCE = "If-Modified-Since"
"""The ``Last-Modified`` half of that conditional request.

Both names are spelled once, here, and matched by that spelling rather than
case-insensitively: `_fetch_inventory` is private, the only caller that
gives it validators is `_revalidate`, and both read the names from here. No
code outside this module chooses the casing, so there is none to normalize.
"""


_HOP_DRAIN_LIMIT = 8 * 1024
"""Cap, in bytes, on how much of a redirect hop's body is read and discarded.

A redirect response's body is boilerplate — a couple of hundred bytes of
"moved here" HTML, if anything — but an HTTP/1.1 connection whose body is
left unread cannot be returned to the pool, so leaving it costs a fresh
TCP+TLS handshake on the next hop even when the chain stays on one host.
Draining under this cap buys the connection back; a hop that answers with
more than this is abandoned instead, since reading an unbounded body to
save one handshake is the worse trade. Deliberately unrelated to
``max_content_size``, which governs the terminal inventory body: hop bodies
are discarded and never counted against it.
"""


class _UpstreamFetchError(httpx.HTTPError):
    """An origin inventory fetch failed a check this service makes itself.

    Every way a fetch can fail on Ook's own terms is this one class: an
    oversized body, an empty one, an over-long redirect chain, an exhausted
    time budget, a ``Location`` that resolves to no URL, a ``304``
    answering a request that carried no validator, a host that will not
    resolve, and a hop the SSRF guard refuses. They are one class because
    the plumbing treats them identically — nothing catches any of them
    apart from the others — and splitting them apart cost three edits per
    failure mode (the class, the raise, and a branch in
    `_describe_upstream_error`) of which only the third had to be
    remembered, and forgetting it silently degraded that failure's stored
    detail to `_GENERIC_UPSTREAM_ERROR`.

    An ``httpx.HTTPError`` so these ride the plumbing the transport's own
    failures already ride. Both fetch paths catch that base, so a new
    failure mode needs no catch clause of its own: on the request path it
    is negatively cached and served as a 502, and on the refresh path it is
    a per-inventory skip that leaves the stored copy serving stale rather
    than replacing it with the failure.

    Distinct from `InvalidInventoryUrlError`, which reports something about
    the URL the *client* chose and can fix: a URL httpx cannot build a
    request from, or one that is not ``https`` or points at a non-public
    address. Everything here is upstream's misbehavior or the absence of an
    answer, neither of which a doc author can act on by editing an
    ``intersphinx_mapping`` entry.

    The message is the whole payload, and it must be safe to store and to
    replay: `_describe_upstream_error` returns it verbatim, so it lands on
    the negative-cache row and is served to every client asking for that
    URL for the negative-TTL window. A detail that would report Ook's own
    resolution of an upstream-chosen host is scrubbed before it reaches
    here — see `_UNSAFE_REDIRECT_DETAIL` and `_UNRESOLVABLE_HOST_DETAIL`.
    """


_UNSAFE_REDIRECT_DETAIL = (
    "Upstream redirected the inventory to a disallowed target"
)
"""Client-facing detail for a redirect hop the SSRF guard refused.

Deliberately says nothing about *why* the guard refused. The guard's reason
names the hop's host and, for the non-public-address rejection, what that
host resolves to *from inside the cluster* — and a redirect-hop rejection's
detail is not a transient message to one client: it is stored on the
negative-cache row and replayed in the 502 body to every client for the
whole negative-TTL window. An origin that redirects to an internal name
would otherwise publish Ook's own DNS view. The specific reason is logged
instead, where operators can read it and clients cannot.
"""


_UNSAFE_REFRESH_URL_DETAIL = (
    "The cached inventory URL is no longer allowed to be fetched"
)
"""Client-facing detail for a stored URL the SSRF guard refused on refresh.

Generic for the same reason as `_UNSAFE_REDIRECT_DETAIL`, and about the same
string class: the refresh path's re-guard exists for the DNS-rebinding case,
so its rejection names the host and the address it now resolves to *from
inside the cluster*. The refresh path writes that detail to
``last_fetch_error``, which `get_inventory` replays verbatim in the 502 body
of every request that finds the row a live negative-cache entry. The specific
reason is logged at the failure boundary instead.

The request path's own rejection of a *client-supplied* URL is deliberately
not scrubbed: that detail is a 400 served once to the client that chose the
URL, naming a host it already knows, and it is never stored.
"""


_UNRESOLVABLE_HOST_DETAIL = "The inventory host could not be resolved"
"""Client-facing detail for a URL whose host would not resolve.

A well-formed host that will not resolve *right now* is not a fact about
the URL the way a bad scheme or a private address is — the URL may be
perfectly good and the resolver merely blinking — so it is an upstream
failure rather than the client's bad request. Being negatively cached is
half the point: a 400 escapes `_fetch_and_store`'s handler entirely, so
nothing is stored and every repeat re-pays a full lookup, in a cluster with
no caching resolver where ``ndots`` search expansion multiplies each one.

Generic for the same reason as `_UNSAFE_REFRESH_URL_DETAIL`, and stored in
the same place: this detail goes onto the negative-cache row and is replayed
in the 502 body of every request for the URL inside the negative-TTL window,
while the resolver's own message describes what Ook's resolver saw from
inside the cluster rather than anything a client can act on. The specific
reason is logged at the raise site instead.

It names no host on purpose, and loses nothing by it: on the request path
the client just asked for the URL, and on the refresh path the stored copy
of this detail is only ever replayed to a client asking for that same URL.
"""


_UNCONDITIONAL_304_DETAIL = (
    "Upstream answered 304 Not Modified to an unconditional inventory request"
)
"""Client-facing detail for a 304 answering a validator-less request.

A ``304`` asserts that the validator the client sent still matches, so one
answering a request that sent none asserts nothing about any particular copy
and carries no body to store. Three requests this service makes can be
unconditional: a cold-miss fetch and a refresh whose chain landed on a
terminal other than the one that minted the stored validators (both by
construction), and the refresh of a row with no stored validators at all — a
negative-cache row that aged into the due list — by accident. So is every
hop before the minting terminal on a chain that reaches it. Trusting a
``304`` on any of them would write a content-less success row: neither
servable nor a live negative-cache entry, and able to clobber content a
concurrent cold miss had just stored.
"""


@dataclass(frozen=True, slots=True)
class _InventoryFetch:
    """The terminal response of an origin inventory fetch."""

    response: httpx.Response
    """The terminal response, after any redirect hops were followed."""

    content: bytes | None
    """The terminal response body, or None for a ``304 Not Modified``."""

    final_url: str
    """The URL that produced the terminal response.

    Equal to the requested URL when the chain did not redirect.
    """

    redirect_hops: list[int]
    """Status codes of the redirect responses followed, in order."""

    @property
    def resolved_url(self) -> str | None:
        """The terminal URL when the chain redirected, else None.

        None rather than the requested URL when nothing redirected, so the
        stored column distinguishes "did not redirect" from "redirected
        back to itself".
        """
        return self.final_url if self.redirect_hops else None

    @property
    def resolved_redirect_permanent(self) -> bool | None:
        """Whether every hop in the chain was permanent, or None if no
        redirect.

        Permanence is `ook.domain.redirects.is_permanent_chain`'s call, the
        same one the link checker's ``redirected`` status is drawn from, so
        an identical chain is classified identically by both.
        """
        if not self.redirect_hops:
            return None
        return is_permanent_chain(self.redirect_hops)


class IntersphinxCacheService:
    """Service that serves cached Sphinx ``objects.inv`` inventories.

    This is the deep module for the intersphinx cache: `get_inventory` is
    the single entry point that resolves an origin inventory URL to its
    bytes, fetching from the origin and populating the cache on a miss. Any
    populated cache entry is served from Postgres without contacting
    upstream — a fetch within ``ttl`` is served as a fresh cache hit, an
    older one is served stale (proactive refresh is the background job's
    responsibility) — so the request path never depends on the origin once
    a copy exists.

    Before any upstream fetch the origin URL passes an SSRF guard: it must
    use ``https`` and its host must not resolve to a private, link-local,
    or loopback address. A guarded URL is never fetched and never stored.
    A URL the guard refuses for what it *is* — unparseable, not ``https``,
    or pointing at a non-public address — is a bad client request; a
    well-formed URL whose host simply will not resolve is not the client's
    mistake and is treated as an upstream failure like any other.

    Every upstream fetch is hardened against a hostile or misbehaving
    origin: redirects are followed by hand, one guarded hop at a time and
    bounded by a hop cap (so the SSRF guard cannot be bypassed via an
    upstream ``Location`` and a redirect loop terminates), the whole fetch —
    the guard on the requested URL included — is cancelled at a single time
    budget so neither a long chain nor one stalled hop can outlast it, and
    the terminal response body is streamed under a size cap so an oversized
    inventory is abandoned rather than buffered into memory. An oversized
    response, an empty one, an over-long chain, an exhausted time budget, an
    unusable ``Location``, a ``304`` answering a request that carried no
    validator, and a hop the guard rejects — including one whose host will
    not resolve — are all treated as upstream fetch failures, so every way a
    chain can fail lands in the same negative-cache-and-502 treatment on the
    request path and the same skip-one-inventory treatment on the refresh
    path. A guard-rejected hop's
    stored and served detail is deliberately generic; the guard's specific
    reason, which would report Ook's own resolution of an upstream-chosen
    host, is logged rather than replayed to clients.

    When a fetch does redirect, its terminal URL and whether every hop was
    permanent are stored on the row, so a permanently-moved inventory URL
    can be surfaced from a cache hit without re-contacting the origin. Both
    are rewritten from the chain observed on each fetch — including a
    ``304`` revalidation, which speaks only to the content — and are left
    null on a negative-cache row, which has no resolved chain at all.

    When a cold-miss upstream fetch fails (4xx/5xx, timeout, connection
    error) and there is no cached content to serve, the failure is
    negatively cached for ``negative_ttl`` as a failure-status/no-content
    row and surfaced as an `UpstreamInventoryError`; a repeat request inside
    the window raises again without re-contacting upstream. Negative caching
    never displaces a content-bearing row — the store enforces this: its
    failure-upsert
    (`IntersphinxInventoryStore.upsert_fetch_failure`) skips the write when
    the existing row already has content, so even a concurrent request that
    stores a good copy between this request's cold miss and its failure
    cannot be clobbered by the negative-cache write.

    Parameters
    ----------
    http_client
        The shared HTTP client used to fetch origin inventories.
    inventory_store
        The store for cached inventories.
    session
        The database session backing ``inventory_store``. Only the batch
        `refresh_inventories` entry point uses it, to own its own commit
        boundaries; the request path leaves committing to its caller.
    ttl
        Freshness TTL: a cached inventory whose last fetch is within this
        window is served as a fresh hit; an older one is served stale.
    negative_ttl
        Negative-cache TTL: a cold-miss fetch failure is cached for this
        window, during which a repeat request raises without re-fetching.
    active_window
        Active window for the proactive refresh job: only inventories
        requested by a client within this window are revalidated; older
        ones are skipped until a new request reactivates them.
    logger
        The logger.
    request_timeout
        Time budget for one upstream inventory fetch, on both the cold-miss
        and refresh paths. It bounds the fetch as a whole — the requested
        URL's guard resolution, every redirect hop, every hop target's guard
        resolution, and the terminal body read all share it, and the fetch is
        cancelled when it expires — so a redirect chain costs no more
        wall-clock time than a single non-redirecting fetch, and no single
        stalled step can cost more than the budget. See `_fetch_budget`.
    max_content_size
        Maximum accepted size, in bytes, of an origin inventory response.
        A response whose ``Content-Length`` or streamed body exceeds this
        cap is abandoned and treated as an upstream fetch failure.
    resolve_host
        Hostname resolver used by the SSRF guard, mainly injectable for
        testing. Defaults to asyncio's ``getaddrinfo``.
    """

    def __init__(
        self,
        *,
        http_client: AsyncClient,
        inventory_store: IntersphinxInventoryStore,
        session: AsyncSession,
        ttl: timedelta,
        negative_ttl: timedelta,
        active_window: timedelta,
        logger: BoundLogger,
        request_timeout: timedelta = timedelta(seconds=30),
        max_content_size: int = _DEFAULT_MAX_CONTENT_SIZE,
        resolve_host: HostResolver | None = None,
    ) -> None:
        self._http_client = http_client
        self._inventory_store = inventory_store
        self._session = session
        self._ttl = ttl
        self._negative_ttl = negative_ttl
        self._active_window = active_window
        self._logger = logger
        self._request_timeout = request_timeout.total_seconds()
        self._max_content_size = max_content_size
        self._resolve_host = resolve_host or _default_resolve_host

    async def get_inventory(self, url: str) -> IntersphinxInventory:
        """Resolve an origin inventory URL to its cached record.

        On a cold miss the origin is fetched synchronously, stored, and
        returned. When the URL is already cached with content, the stored
        copy is served without contacting upstream and its last-requested
        time is bumped — a fetch within the TTL is a fresh cache hit, an
        older one is served stale.

        Parameters
        ----------
        url
            The full origin ``objects.inv`` URL.

        Returns
        -------
        IntersphinxInventory
            The cached inventory record for the URL.

        Raises
        ------
        UpstreamInventoryError
            Raised on a cold-miss upstream fetch failure, and on a repeat
            request served from the negative cache within the negative TTL.
        """
        cached = await self._inventory_store.get_inventory(url)
        if cached is not None and cached.content is not None:
            now = datetime.now(tz=UTC)
            await self._inventory_store.touch_date_requested(url, now=now)
            self._log_cache_serve(cached, now=now)
            return replace(cached, date_requested=now)
        if cached is not None and self._is_negative_cache_fresh(cached):
            self._logger.info(
                "Serving negatively-cached intersphinx inventory failure",
                url=url,
                cache_status="negative",
                error=cached.last_fetch_error,
            )
            raise UpstreamInventoryError(
                cached.last_fetch_error or _GENERIC_UPSTREAM_ERROR
            )
        return await self._fetch_and_store(url)

    async def refresh_inventories(
        self, *, now: datetime | None = None, limit: int | None = None
    ) -> IntersphinxRefreshSummary:
        """Proactively revalidate stale, still-active cached inventories.

        Each inventory past the freshness TTL that a client requested within
        the active window is revalidated with a conditional GET carrying its
        stored ``ETag`` (as ``If-None-Match``) and ``Last-Modified`` (as
        ``If-Modified-Since``) — but only to the terminal that minted them,
        since a ``304`` from anywhere else would revalidate some other
        resource than the one the cache holds. A ``304`` keeps the stored
        content and bumps ``date_fetched``; a ``200``, which is all a chain
        landing anywhere else can answer with, replaces the content and
        validators. See `_revalidate`. Inventories requested longer ago than
        the active window are skipped, not deleted — a new client request
        reactivates them via ``date_requested``.

        A per-inventory failure (SSRF guard rejection, upstream 4xx/5xx,
        timeout, connection error) is logged and skipped, and the rest of the
        batch continues. The stored copy — content, validators, and the
        ``date_fetched`` freshness anchor alike — is left untouched so it
        keeps serving stale at its true age; only the failure columns and the
        backoff marker are written, which holds the inventory out of the due
        list for one TTL. Without that backoff a broken origin would be
        retried on every run, sorting ahead of every healthy inventory and
        (since each attempt can walk a whole redirect chain) delaying them
        behind it, for the entire active window. This is the background
        counterpart to the request path: the request path never blocks on
        upstream because this job keeps the cache warm.

        Every row's outcome is timestamped when it is written, not when the
        batch started. The batch is serial and each inventory can spend the
        whole fetch budget, so a run's own duration is unbounded by anything
        but its inventory count; dating outcomes from the batch's start would
        shave that duration off each row's next interval, and a failure deep
        in a long run would be back in the very next run's due list.

        Every write here — the success, the revalidation, and the failure
        alike — is guarded on the ``date_fetched`` the due-list read saw, and
        dropped outright if a client cold miss refreshed the row while this
        batch's fetch was in flight. A row in the due list is stale by
        construction, so that miss is a real race, and each of this run's
        outcomes describes the copy it started from rather than the one the
        miss stored: applying it would revert good content, or leave fresh
        content behind a stale status and a backoff marker. A dropped
        success or revalidation is counted as ``superseded``, and a dropped
        failure is reported as ``backoff_recorded=False``, so neither is
        silent.

        Recording a failure is itself a guarded write against a contended
        row, so it can fail too. That is caught, logged, and counted as
        ``unrecorded_failures`` rather than allowed out of the loop: a
        bookkeeping error must not cost the batch every inventory behind it.
        See `_record_refresh_failure`.

        Unlike the rest of this service, which leaves transaction boundaries
        to its caller (the request handler commits `get_inventory` itself),
        this batch-job entry point owns its own commits and must be called
        without a surrounding transaction. The due-list selection is committed
        as its own short transaction, then each inventory's outcome is
        committed as soon as its refresh completes. So no DB transaction (and
        none of the row locks its upsert takes) is held open across an HTTP
        fetch that may run for the full request timeout, and a mid-run crash
        preserves the outcomes of every inventory already refreshed rather
        than discarding the whole batch.

        Parameters
        ----------
        now
            The reference time for the staleness and active-window cutoffs.
            Defaults to the current time.
        limit
            The maximum number of inventories to refresh in this run, or
            None for no limit.

        Returns
        -------
        IntersphinxRefreshSummary
            Counts of the inventories considered, refreshed, revalidated,
            superseded, and failed, plus those failures whose own
            bookkeeping write failed.
        """
        if now is None:
            now = datetime.now(tz=UTC)
        due = await self._inventory_store.get_stale_active_inventories(
            now=now,
            ttl=self._ttl,
            active_window=self._active_window,
            limit=limit,
        )
        # Commit the selection as its own short transaction so no read lock or
        # snapshot is held open across the per-inventory HTTP fetches below.
        await self._session.commit()
        results: Counter[_RefreshResult] = Counter()
        failed = 0
        unrecorded = 0
        for inventory in due:
            try:
                result = await self._refresh_one(inventory)
            except (httpx.HTTPError, InvalidInventoryUrlError) as exc:
                # No rollback: every failure this catches is raised before
                # `_refresh_one` writes anything, so the session is idle
                # here. A store write that fails raises `SQLAlchemyError`
                # instead, which is not caught here and does not belong to
                # a per-inventory skip. The stored copy is untouched either
                # way and keeps serving stale.
                failed += 1
                detail = self._describe_refresh_error(exc, url=inventory.url)
                backoff = await self._record_refresh_failure(
                    inventory, detail=detail
                )
                if backoff is _BackoffWrite.failed:
                    unrecorded += 1
                self._logger.warning(
                    "Failed to refresh intersphinx inventory",
                    url=inventory.url,
                    cache_status="refresh-failure",
                    error=detail,
                    backoff_recorded=backoff is _BackoffWrite.recorded,
                )
                continue
            # Commit this inventory's outcome immediately so a later crash in
            # the batch cannot lose it and no transaction spans the next fetch.
            await self._session.commit()
            results[result] += 1
        summary = IntersphinxRefreshSummary(
            considered=len(due),
            refreshed=results[_RefreshResult.refreshed],
            revalidated=results[_RefreshResult.revalidated],
            superseded=results[_RefreshResult.superseded],
            failed=failed,
            unrecorded_failures=unrecorded,
        )
        self._logger.info(
            "Completed intersphinx inventory refresh",
            considered=summary.considered,
            refreshed=summary.refreshed,
            revalidated=summary.revalidated,
            superseded=summary.superseded,
            failed=summary.failed,
            unrecorded_failures=summary.unrecorded_failures,
        )
        return summary

    async def _record_refresh_failure(
        self, inventory: IntersphinxInventory, *, detail: str
    ) -> _BackoffWrite:
        """Record one inventory's failed refresh in its own transaction.

        Writing the failure gives the inventory its ``date_refresh_failed``
        backoff marker, which holds it out of the due list for one TTL so a
        broken origin is retried on the normal cadence instead of heading
        every run. The write is stamped now rather than at batch start, so
        the backoff runs from this attempt, and guarded on the freshness
        anchor the due-list read saw, so a row a client cold miss refreshed
        under this run is left alone.

        A database error here is caught rather than allowed to propagate.
        This is bookkeeping *about* a failure, in the handler whose whole
        purpose is that one inventory's failure never stops the batch, and it
        races the very cold miss the guard defends against — so a
        serialization or deadlock error is the expected contention, not a
        remote possibility. Letting it out would strand every inventory after
        this one unrefreshed, skip the end-of-run summary, and leave this row
        with its old ``date_fetched`` and no backoff marker, at the head of
        the next run's due list, ready to abort that batch the same way.

        Parameters
        ----------
        inventory
            The inventory whose refresh failed, as the due-list read saw it.
        detail
            The client-facing failure description to store.

        Returns
        -------
        _BackoffWrite
            Whether the backoff marker was recorded, deliberately dropped by
            the concurrency guard, or lost to a failed write.
        """
        try:
            recorded = await self._inventory_store.update_refresh_failure(
                inventory.url,
                now=datetime.now(tz=UTC),
                error=detail,
                expected_date_fetched=inventory.date_fetched,
            )
            await self._session.commit()
        except SQLAlchemyError as exc:
            # Return the session to a usable state for the next inventory:
            # a failed statement or commit leaves its transaction unusable
            # until it is rolled back.
            await self._session.rollback()
            self._logger.exception(
                "Failed to record an intersphinx refresh failure",
                url=inventory.url,
                cache_status="refresh-failure",
                error=detail,
                bookkeeping_error=str(exc),
            )
            return _BackoffWrite.failed
        return _BackoffWrite.recorded if recorded else _BackoffWrite.dropped

    def _describe_refresh_error(
        self, error: httpx.HTTPError | InvalidInventoryUrlError, *, url: str
    ) -> str:
        """Describe a refresh failure for storage in ``last_fetch_error``.

        This is the refresh path's error boundary, and the scrub of the
        guard's rejection reason lives here rather than at the raise site:
        `_guard_url` is shared with the request path, whose 400 for a
        client-supplied URL keeps the specific, actionable reason. Only the
        stored detail — replayed to every client for a negative-cache row's
        whole TTL — has to be generic, so only this side scrubs, and the
        reason is logged here in the same record shape
        `_guard_redirect_url` uses for a refused hop.

        Parameters
        ----------
        error
            The failure raised by `_refresh_one`.
        url
            The inventory URL that failed, for the rejection log.

        Returns
        -------
        str
            The detail to store on the row and serve to clients.
        """
        if isinstance(error, httpx.HTTPError):
            return _describe_upstream_error(error)
        self._logger.warning(
            "Rejected a stored intersphinx inventory URL by SSRF guard",
            url=url,
            cache_status="refresh-failure",
            reason=str(error),
        )
        return _UNSAFE_REFRESH_URL_DETAIL

    async def _refresh_one(
        self, inventory: IntersphinxInventory
    ) -> _RefreshResult:
        """Revalidate one cached inventory against its origin.

        Returns which way the refresh ended: a ``304`` revalidated the stored
        copy in place, a ``200`` replaced its content, or the write was
        dropped because the row changed under this fetch. Raises on a guard
        rejection, an upstream failure, an exhausted time budget, or an
        oversized response so the caller can log the failure, record its
        backoff, and skip to the next inventory, leaving the stored copy
        untouched.

        Both writes are guarded on the ``date_fetched`` the due-list read saw
        and report whether they landed: a row in the due list is stale by
        construction, so a client cold miss can commit good content inside
        this fetch's window, and this refresh's outcome describes the copy it
        started from rather than the one that miss stored.

        Everything that talks to the network — the stored URL's re-guard and
        the revalidation chain alike — runs inside one `_fetch_budget`, so
        one origin (or one hung resolver) costs its own inventory a skip
        rather than stalling the serial batch behind it. The database writes
        below deliberately sit outside the budget: they are this inventory's
        recorded outcome and must not be cancelled by it.

        ``date_fetched`` is read after the fetch rather than taken from the
        batch's reference time: the batch is serial and each inventory can
        spend the whole fetch budget, so a run is as long as its slowest
        origins make it, and dating a copy from the batch's start would
        report it older than it is by however long the batch had run.
        """
        async with self._fetch_budget() as deadline:
            # Re-guard the stored URL before fetching: it passed the guard
            # when first cached, but DNS can rebind a once-public host to a
            # private address, so the cheap re-check preserves the SSRF
            # invariant.
            await self._guard_url(inventory.url)
            fetch = await self._revalidate(inventory, deadline=deadline)
        response = fetch.response
        # Every field a successful refresh writes regardless of whether the
        # content changed, applied once so the next one added cannot land on
        # only one of the two branches below. The 200 branch layers the
        # changed content on top of this.
        outcome = replace(
            inventory,
            date_fetched=datetime.now(tz=UTC),
            last_fetch_status=InventoryFetchStatus.success,
            last_fetch_error=None,
            # A 304 says the content is unchanged, which says nothing about
            # the chain: record the one this revalidation walked rather than
            # carrying the stored one forward.
            resolved_url=fetch.resolved_url,
            resolved_redirect_permanent=fetch.resolved_redirect_permanent,
            # A revalidated inventory is healthy again: drop any backoff an
            # earlier failure left on it.
            date_refresh_failed=None,
        )
        if response.status_code == 304:
            # Write only what a revalidation changes: date_requested belongs
            # to the request path, and the content the 304 just said did not
            # move stays as the last 200 wrote it.
            landed = await self._inventory_store.update_revalidation_outcome(
                outcome, expected_date_fetched=inventory.date_fetched
            )
            result = _RefreshResult.revalidated
            message = "Revalidated intersphinx inventory (304 Not Modified)"
        else:
            response.raise_for_status()
            landed = await self._inventory_store.update_refresh_outcome(
                replace(
                    outcome,
                    content=fetch.content,
                    content_type=response.headers.get("Content-Type"),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                ),
                expected_date_fetched=inventory.date_fetched,
            )
            result = _RefreshResult.refreshed
            message = "Refreshed intersphinx inventory (200 OK)"
        if not landed:
            result = _RefreshResult.superseded
            message = "Dropped a superseded intersphinx refresh outcome"
        self._logger.info(
            message,
            url=inventory.url,
            cache_status=result.name,
            final_url=fetch.final_url,
            redirect_hops=len(fetch.redirect_hops),
        )
        return result

    async def _revalidate(
        self, inventory: IntersphinxInventory, *, deadline: float
    ) -> _InventoryFetch:
        """Fetch one cached inventory conditionally, but only where it holds.

        The conditional request carries the stored ``ETag`` (as
        ``If-None-Match``) and ``Last-Modified`` (as ``If-Modified-Since``),
        but those validators were minted by the terminal of the chain
        observed at the *last* fetch, and nothing pins the chain in place:
        upstream can re-point an alias at a different resource between
        refreshes. Sending them blind is what lets a moved chain answer a
        false ``304`` that marks the wrong bytes fresh forever, so they are
        sent only to the terminal the stored copy was fetched from — which
        `_fetch_inventory` enforces per hop, from the ``validator_url`` this
        method hands it. A chain that lands anywhere else gets an
        unconditional request and has to answer with the bytes, so the row
        is repaired by the same single walk that discovered the move.

        Withholding rather than discarding is what bounds the cost of a
        terminal that keeps moving. An origin whose ``Location`` carries a
        per-response token or a load-balancer shard never lands twice on the
        same URL, so detecting the mismatch from the response and re-walking
        the chain to repair it would cost that inventory two chain walks and
        a body on every run, forever, out of one `_fetch_budget` — the
        likeliest way for a healthy inventory to exhaust its budget and take
        a backoff. Not asking a question whose answer cannot be trusted
        costs one chain and one body instead: exactly what an unconditional
        refresh costs, however long the terminal keeps varying.

        ``resolved_url`` is null for two different rows and both are treated
        as "terminal not recorded" rather than as "the terminal is the
        requested URL". One is a row whose last chain did not redirect; the
        other is any row cached before the column existed, which has no
        backfill (see the ``4acb43afff3d`` migration). Conflating them would
        pin a redirecting pre-column row's validators to the one URL its
        terminal is guaranteed *not* to be, so every such row would pay a
        full re-download on its first refresh under this code. The residual
        exposure is the row that genuinely did not redirect and whose URL
        has since started redirecting: its validators do reach the new
        terminal, and a false ``304`` there would be trusted once. It is
        once, and only once — that outcome records the terminal it came
        from, so every later refresh of that row is held to it.

        `_fetch_inventory` complements the withholding by dropping
        ``If-Modified-Since`` as soon as a hop redirects, which covers the
        rows whose terminal is not recorded and so cannot be held to one.

        Neither this fetch nor any other can come back as a ``304`` the
        caller must not trust: `_fetch_inventory` rejects a ``304``
        answering a request that carried no validator, which covers the
        withheld case above as well as a row that had no validators to send
        in the first place — a negative-cache row that aged into the due
        list.

        Raises
        ------
        _UpstreamFetchError
            Raised, from `_fetch_inventory`, when the origin answers 304 to
            a request that carried no validator.
        """
        validators: dict[str, str] = {}
        if inventory.etag is not None:
            validators[_IF_NONE_MATCH] = inventory.etag
        if inventory.last_modified is not None:
            validators[_IF_MODIFIED_SINCE] = inventory.last_modified
        fetch = await self._fetch_inventory(
            inventory.url,
            validators=validators,
            validator_url=inventory.resolved_url,
            deadline=deadline,
        )
        if (
            inventory.resolved_url is not None
            and fetch.final_url != inventory.resolved_url
        ):
            self._logger.info(
                "Intersphinx chain moved since the last inventory fetch",
                url=inventory.url,
                cache_status="chain-moved",
                stored_url=inventory.resolved_url,
                final_url=fetch.final_url,
            )
        return fetch

    def _is_negative_cache_fresh(self, cached: IntersphinxInventory) -> bool:
        """Return whether a cached row is a live negative-cache entry.

        A negative-cache entry is a failure-status row with no content whose
        last fetch is within the negative TTL.
        """
        if cached.content is not None:
            return False
        if cached.last_fetch_status is not InventoryFetchStatus.failure:
            return False
        if cached.date_fetched is None:
            return False
        return datetime.now(tz=UTC) - cached.date_fetched <= self._negative_ttl

    def _log_cache_serve(
        self, inventory: IntersphinxInventory, *, now: datetime
    ) -> None:
        """Emit a structured cache-hit or stale-serve log for a served copy.

        A copy fetched within the TTL is a fresh hit; a copy with no fetch
        time or a fetch older than the TTL is served stale.
        """
        is_fresh = (
            inventory.date_fetched is not None
            and now - inventory.date_fetched <= self._ttl
        )
        if is_fresh:
            self._logger.info(
                "Serving fresh intersphinx inventory from cache",
                url=inventory.url,
                cache_status="hit",
            )
        else:
            self._logger.info(
                "Serving stale intersphinx inventory from cache",
                url=inventory.url,
                cache_status="stale",
            )

    async def _fetch_and_store(self, url: str) -> IntersphinxInventory:
        """Fetch an origin inventory and store it (the cold-miss path).

        On an upstream failure with no cached content to fall back on, the
        failure is negatively cached and re-raised as an
        `UpstreamInventoryError`.
        """
        try:
            async with self._fetch_budget() as deadline:
                await self._guard_url(url)
                self._logger.info(
                    "Fetching intersphinx inventory on cache miss", url=url
                )
                fetch = await self._fetch_inventory(url, deadline=deadline)
                fetch.response.raise_for_status()
        except httpx.HTTPError as exc:
            await self._store_failure(url, error=exc)
        response = fetch.response
        self._logger.info(
            "Fetched intersphinx inventory from origin",
            url=url,
            cache_status="miss",
            final_url=fetch.final_url,
            redirect_hops=len(fetch.redirect_hops),
        )
        now = datetime.now(tz=UTC)
        inventory = IntersphinxInventory(
            url=url,
            content=fetch.content,
            content_type=response.headers.get("Content-Type"),
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            date_fetched=now,
            date_requested=now,
            last_fetch_status=InventoryFetchStatus.success,
            last_fetch_error=None,
            resolved_url=fetch.resolved_url,
            resolved_redirect_permanent=fetch.resolved_redirect_permanent,
            # A fetch that succeeded owes the refresh job no backoff.
            date_refresh_failed=None,
        )
        await self._inventory_store.upsert_inventory(inventory)
        return inventory

    async def _fetch_inventory(
        self,
        url: str,
        *,
        deadline: float,
        validators: Mapping[str, str] | None = None,
        validator_url: str | None = None,
    ) -> _InventoryFetch:
        """Fetch an origin inventory, following redirects under the guard.

        Redirects are followed by hand rather than by httpx, one hop at a
        time, so the SSRF guard runs against every hop target before it is
        fetched and cannot be bypassed by an upstream ``Location``. A
        relative ``Location`` is resolved against the hop that sent it, not
        against the originally requested URL. The chain is bounded by
        `MAX_REDIRECTS` hops so a redirect loop terminates. Every *new* host
        in the chain is guarded; a host the guard already accepted within
        this same fetch is not resolved again, which is what keeps a chain
        that leaves a host and returns from paying for that host twice.

        Only the terminal response's body is kept: a redirect hop's body is
        discarded and never counted against the size cap, and both the
        ``Content-Length`` pre-check and the streamed-size cap apply to the
        terminal response alone. The terminal body is streamed so an
        oversized response is abandoned as soon as the cap is exceeded
        rather than fully buffered, and a terminal answering a success
        status with no body at all is rejected rather than stored as
        content. Only a *success* status is checked for emptiness, so an
        empty 4xx or 5xx still reports the status code its caller's
        ``raise_for_status()`` raises on. A hop's
        body is discarded by *reading* it, under `_HOP_DRAIN_LIMIT` — see
        `_drain_hop_body`, which explains why the read is what keeps a chain
        from opening a connection per hop.

        The conditional-request headers, if any, are sent only where they
        hold. A validator asserts something about one resource, and a chain
        can be re-pointed between fetches, so when the caller names the URL
        that minted them in ``validator_url`` they ride only the hop aimed
        at that URL: a chain that lands anywhere else is asked
        unconditionally and has to answer with the body, which is what makes
        a false ``304`` from a moved terminal impossible rather than merely
        detectable. Withholding also bounds an origin whose terminal never
        repeats — a per-response token, a load-balancer shard — to one chain
        and one body per fetch, where detecting the mismatch afterwards
        would cost a second walk of the same chain every time.

        A caller that does not know which URL minted them passes no
        ``validator_url``, and they are re-sent on every hop so a chain
        ending in a ``304 Not Modified`` still revalidates — except
        ``If-Modified-Since``, which is dropped as soon as a hop redirects.
        A modification date is only meaningful against the resource it was
        read from, and with no minting URL to hold it to, a redirect is the
        last point at which the request is known to be aimed there; past it
        the terminal may be an entirely different — possibly older —
        resource, which would answer a false ``304``. ``If-None-Match`` is
        kept: a strong validator stays trustworthy wherever the chain lands,
        and RFC 9110 §13.1.3 gives it precedence regardless.

        A ``304`` is returned to the caller only when the request that drew
        it actually carried a validator; otherwise it is upstream
        misbehavior and raises. Enforcing that here rather than in the
        callers covers every way the terminal request can end up
        unconditional — no validators were stored, the chain landed
        somewhere other than the ``validator_url`` they are held to, or the
        only one was ``If-Modified-Since`` and the chain dropped it — and
        gives every caller the invariant that a returned ``304`` is a real
        revalidation of the copy whose validators were sent.

        The chain runs inside the caller's `_fetch_budget`, which cancels it
        outright at the deadline; that cancellation, not any check here, is
        what bounds the fetch. ``deadline`` is the same budget's monotonic
        expiry, and its one use is to size each hop's per-call httpx
        timeout — which is also what stops a hop the budget can no longer
        pay for from being started, and the reason a slow chain usually
        reports a spent budget rather than being cut mid-syscall.

        Parameters
        ----------
        url
            The origin inventory URL to fetch.
        deadline
            The enclosing `_fetch_budget`'s monotonic expiry.
        validators
            The conditional-request validators to send, if any, keyed by
            `_IF_NONE_MATCH` and `_IF_MODIFIED_SINCE`. These are the only
            headers this fetch ever sends, which is what lets a request
            carrying none of them be recognized as unconditional by the
            mapping being empty.
        validator_url
            The URL that minted ``validators``, when the caller knows it.
            They are then sent only to that URL and to no other hop in the
            chain. None means the minting URL is unknown, and they ride
            every hop.

        Raises
        ------
        _UpstreamFetchError
            Raised when the terminal response carries a success status and
            an empty body (including a ``204``); when its body exceeds the
            size cap by its ``Content-Length`` or by its streamed size;
            when the chain outlasts the whole-fetch time budget; when a
            hop's ``Location`` cannot be resolved to a URL, whether by this
            service's own join or by the one httpx runs inside the request;
            when the terminal answers ``304`` to a request that carried no
            validator; when the chain exceeds `MAX_REDIRECTS` hops; and
            when a redirect hop's target fails the SSRF guard, including
            when its host cannot be resolved.
        httpx.HTTPError
            Propagated from the transport on a timeout or connection error.
        """
        current_url = url
        # This fetch's own copy, since a hop can strip a validator from it
        # and the caller's mapping is not this method's to edit.
        carried_validators = dict(validators or {})
        hops: list[int] = []
        # Seeded with the requested URL's host, which both callers guard
        # immediately before this fetch, so a chain that comes back to it
        # does not resolve it a second time. Local to this fetch: a host is
        # skipped only within the chain that just validated it.
        validated_hosts = {host} if (host := httpx.URL(url).host) else set()
        while True:
            # A validator is an assertion about one resource, so it is sent
            # only where it holds: to the URL that minted it, or anywhere at
            # all when the caller does not know which URL that was.
            request_validators = (
                carried_validators
                if validator_url is None or current_url == validator_url
                else {}
            )
            try:
                async with self._http_client.stream(
                    "GET",
                    current_url,
                    headers=request_validators,
                    follow_redirects=False,
                    timeout=self._remaining_budget(deadline),
                ) as response:
                    location = _first_location(response.headers)
                    if (
                        response.status_code not in REDIRECT_CODES
                        or not location
                    ):
                        if response.status_code == 304:
                            if not request_validators:
                                raise _UpstreamFetchError(
                                    _UNCONDITIONAL_304_DETAIL
                                )
                            return _InventoryFetch(
                                response, None, current_url, hops
                            )
                        self._check_content_length(response)
                        content = await self._read_capped_body(response)
                        if response.is_success and not content:
                            # Storing it would write a success row whose
                            # content is b"": every later decision point
                            # tests ``content is not None``, so such a row
                            # is a permanent cache hit — never a cold miss
                            # again, never a live negative-cache entry, and
                            # immune to the store's ``content IS NULL``
                            # guard on a failure upsert. Refusing the fetch
                            # is the whole fix.
                            raise _UpstreamFetchError(
                                "Upstream returned an empty inventory body"
                                f" with HTTP {response.status_code}"
                            )
                        return _InventoryFetch(
                            response, content, current_url, hops
                        )
                    if len(hops) >= MAX_REDIRECTS:
                        # Give up on the hop count alone, before joining or
                        # guarding a target this fetch will never request,
                        # so the reported failure never depends on a URL
                        # already ruled out.
                        raise _UpstreamFetchError(
                            f"Exceeded {MAX_REDIRECTS} redirects"
                        )
                    hops.append(response.status_code)
                    next_url = _join_redirect_url(current_url, location)
                    await self._drain_hop_body(response)
            except httpx.InvalidURL as exc:
                # httpx joins a 3xx ``Location`` itself even under
                # ``follow_redirects=False``, so a target that only
                # overflows the URL length limit once joined raises from
                # inside the request rather than from `_join_redirect_url`,
                # which never gets a response to inspect. Converted here in
                # the hop loop, rather than at each fetch path's error
                # boundary, so both paths classify it without a catch of
                # their own and without widening the stored-failure helpers
                # past the taxonomy they describe.
                # ``httpx.InvalidURL`` is not an ``httpx.HTTPError``, so
                # left alone it escapes the cold-miss handler as an
                # unhandled 500 that caches nothing, and aborts the whole
                # refresh batch on one hostile origin.
                raise _UpstreamFetchError(
                    _malformed_redirect_detail(exc)
                ) from exc
            if validator_url is None:
                # With no minting URL to hold the validators to, a redirect
                # is the last point at which the request is known to be
                # aimed at the URL the stored modification date came from,
                # so stop sending it here. When the minting URL *is* known
                # the check above already sends it nowhere else, and
                # stripping it as well would drop a validator that is safe
                # exactly where it lands.
                carried_validators.pop(_IF_MODIFIED_SINCE, None)
            # Guard outside the stream context so the hop's connection is
            # released — back to the pool, its body already drained — before
            # the guard's DNS lookup and the next hop's request.
            await self._guard_redirect_url(
                next_url,
                requested_url=url,
                validated_hosts=validated_hosts,
            )
            current_url = next_url

    async def _drain_hop_body(self, response: httpx.Response) -> None:
        """Read and discard a redirect hop's body so its connection is reused.

        Exiting a streamed response with its body unread leaves the HTTP/1.1
        connection unusable, so the pool drops it and the next hop pays a
        fresh TCP+TLS handshake — three of them, on the motivating
        ``www`` → ``docs`` → ``docs`` chain, to one host, on every cold miss
        and every refresh. Reading the (tiny) body to the end lets the
        connection go back in the pool for the next hop instead.

        Bounded by `_HOP_DRAIN_LIMIT`: a hop answering with more than that is
        left unread from that point on, so the fetch abandons the connection
        rather than reading an unbounded body to save one handshake. The
        drained bytes are discarded and never counted against
        ``max_content_size``, which governs the terminal body alone.

        The whole-fetch budget needs no check of its own here. This runs
        inside `_fetch_budget`, whose `asyncio.timeout` cancels the fetch at
        the deadline, and a hop dribbling its body out suspends on every
        read — which is exactly where that cancellation lands. The only read
        that never suspends is one served from a buffer, and the cap above
        bounds that one.
        """
        drained = 0
        async for chunk in response.aiter_bytes():
            drained += len(chunk)
            if drained > _HOP_DRAIN_LIMIT:
                return

    @asynccontextmanager
    async def _fetch_budget(self) -> AsyncIterator[float]:
        """Run one inventory fetch under a single cancelling time budget.

        Yields the budget's monotonic expiry, which the body passes down to
        size each per-call httpx timeout. The budget itself is enforced by
        `asyncio.timeout`, which cancels the whole body at the deadline —
        the only enforcement that actually holds. Checking the clock between
        operations cannot bound one that never returns, and the per-call
        httpx timeouts cannot either: httpcore re-arms the read timeout on
        every socket read, so an origin trickling response-header bytes
        keeps a single hop alive indefinitely, and connect, write, and read
        each get the full remaining budget in turn. On the cold-miss path
        that hop holds the request's DB session open the whole time, which
        is the exhaustion hazard the budget exists to close.

        The budget covers the SSRF guard on the requested URL as well as the
        chain: that resolution runs before the first hop, and the cluster
        has no caching resolver, so an ``ndots``-expanded lookup of a host
        whose DNS never answers is exactly the kind of stall that must not
        sit outside every bound.

        Callers put their database writes *outside* this block. A write is
        the recorded outcome of the fetch — a negative-cache row, a refresh
        result — and cancelling it would lose the very record the spent
        budget is supposed to produce.

        Yields
        ------
        float
            The budget's monotonic deadline.

        Raises
        ------
        _UpstreamFetchError
            Raised when the budget expires, so an exhausted budget reaches
            both fetch paths as the same upstream failure however it was
            spent: negatively cached and served as a 502 on the request
            path, a per-inventory skip on the refresh path.
        """
        deadline = time.monotonic() + self._request_timeout
        try:
            async with asyncio.timeout(self._request_timeout):
                yield deadline
        except TimeoutError as exc:
            raise self._deadline_error() from exc

    def _remaining_budget(self, deadline: float) -> float:
        """Return the seconds left in the whole-fetch budget.

        Sizes the per-hop httpx timeout, which is this value's only use:
        the budget is enforced by `_fetch_budget`'s cancellation, so a check
        of the clock is worth making only where its answer is needed as a
        number.

        Raises
        ------
        _UpstreamFetchError
            Raised when the budget is already spent, so a hop the budget
            can no longer pay for is not started at all — and never asked
            for with a non-positive timeout.
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise self._deadline_error()
        return remaining

    def _deadline_error(self) -> _UpstreamFetchError:
        """Build the spent-budget error carrying the configured budget."""
        return _UpstreamFetchError(
            "Upstream inventory fetch exceeded its time budget of "
            f"{self._request_timeout:g} s"
        )

    def _check_content_length(self, response: httpx.Response) -> None:
        """Abort before reading the body if ``Content-Length`` is over cap."""
        raw_length = response.headers.get("Content-Length")
        if raw_length is None:
            return
        try:
            declared = int(raw_length)
        except ValueError:
            return
        if declared > self._max_content_size:
            raise self._too_large_error()

    async def _read_capped_body(self, response: httpx.Response) -> bytes:
        """Stream the body, abandoning it as soon as it passes the size cap.

        Streamed rather than buffered so an oversized inventory is dropped
        at the cap instead of being read into memory in full.

        The whole-fetch budget needs no check of its own here, for the same
        reason as in `_drain_hop_body`: a body dribbled out a byte at a time
        suspends on every read, which is where `_fetch_budget`'s
        cancellation lands, and the cap bounds the read that does not
        suspend.
        """
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self._max_content_size:
                raise self._too_large_error()
            chunks.append(chunk)
        return b"".join(chunks)

    def _too_large_error(self) -> _UpstreamFetchError:
        """Build the oversized-response error carrying the configured cap."""
        return _UpstreamFetchError(
            "Upstream inventory exceeds the size cap of "
            f"{_format_size_cap(self._max_content_size)}"
        )

    async def _store_failure(
        self, url: str, *, error: httpx.HTTPError
    ) -> NoReturn:
        """Negatively cache a cold-miss upstream failure and raise.

        The failure is stored as a failure-status row with no content via
        `IntersphinxInventoryStore.upsert_fetch_failure`, whose write is
        skipped when a content-bearing row already exists (a concurrent
        request may have stored a good copy meanwhile). Either way the
        request path surfaces the failure as an `UpstreamInventoryError`.
        """
        detail = _describe_upstream_error(error)
        now = datetime.now(tz=UTC)
        await self._inventory_store.upsert_fetch_failure(
            IntersphinxInventory(
                url=url,
                content=None,
                content_type=None,
                etag=None,
                last_modified=None,
                date_fetched=now,
                date_requested=now,
                last_fetch_status=InventoryFetchStatus.failure,
                last_fetch_error=detail,
                # A negative-cache row has no content and no resolved chain,
                # so the resolved-redirect fields keep their None defaults.
                # This row's own date_fetched dates the failed attempt, so
                # the refresh job's backoff marker — which exists only
                # because a refresh failure must not touch date_fetched —
                # has nothing to add here.
                date_refresh_failed=None,
            )
        )
        self._logger.warning(
            "Intersphinx inventory upstream fetch failed on cache miss",
            url=url,
            cache_status="miss",
            error=detail,
        )
        raise UpstreamInventoryError(detail)

    def _parse_url(self, url: str) -> httpx.URL:
        """Parse a URL for the guard, rejecting one httpx cannot request.

        Parsed with ``httpx.URL`` and with nothing else, because httpx is
        what the fetch actually connects with: a second parser's reading is
        not the one the socket obeys, so validating a host httpx would
        never have asked for is a gap no amount of agreement closes. It is
        also the stricter of the two readings on the shapes that matter —
        it refuses a bogus port (``https://example.com:notaport/objects.inv``,
        which ``urlsplit`` waves through) and an unterminated IPv6 literal
        alike — and refusing them here, before anything is resolved, is
        what makes them the 400 the error taxonomy assigns a bad requested
        URL rather than an ``httpx.InvalidURL`` escaping every fetch path's
        handler as an unhandled 500 that caches nothing.

        The line this draws is "a URL httpx cannot build a request from".
        A netloc ``urlsplit`` alone refuses — a stray bracket, say — is
        percent-encoded by httpx into a host that simply does not exist, so
        it is refused a step later by resolution, as the negatively cached
        upstream failure every unresolvable host is. That is one fewer
        parser and one fewer taxonomy for a shape no inventory URL has.

        This is the requested URL's counterpart to `_join_redirect_url`,
        which closes the same escape for a hop's ``Location``.

        Returns
        -------
        httpx.URL
            The parse the guard's own checks and the fetch both read.

        Raises
        ------
        InvalidInventoryUrlError
            Raised when httpx refuses the URL.
        """
        try:
            return httpx.URL(url)
        except httpx.InvalidURL as exc:
            self._reject_url(url, f"URL could not be parsed: {exc}")

    async def _guard_url(
        self, url: str, *, validated_hosts: set[str] | None = None
    ) -> None:
        """Reject a URL that must not be fetched from upstream.

        This SSRF guard runs before any upstream fetch: the URL must parse,
        it must use ``https``, and its host must not resolve to a private,
        link-local, or loopback address. A rejected URL is never fetched and
        never stored.

        It refuses a URL in two distinguishable ways, and they are not the
        same kind of failure. Everything above is a fact about the URL the
        caller chose, so it is a bad client request (400) — see
        `_reject_url`. A host that cannot be resolved at all is the absence
        of an answer rather than a fact about the URL, so it is an upstream
        failure (502, negatively cached) — see `_fail_resolution`.

        Parsing comes first, and rejects before anything is resolved — see
        `_parse_url`.

        The guard resolves the host itself, but httpx re-resolves at connect
        time, so a DNS-rebinding answer could point the socket at a private
        address in the window between this check and the connect. The
        sibling link-check checker closes that window by pinning the
        validated IP; here resolution is treated as advisory instead.
        Because the fetch is ``https``-only, a host rebound to an internal
        target between guard and connect would still have to present a TLS
        certificate valid for the original hostname, and httpx's TLS
        hostname verification would reject it. TLS hostname verification on
        the https-only fetch is what backstops rebinding, so IP pinning is
        unnecessary here.

        Parameters
        ----------
        url
            The URL to validate.
        validated_hosts
            Hosts this guard already accepted earlier in the same fetch, if
            any. A host in the set skips *resolution* only — the scheme
            check still runs on every URL, since https-only is what
            backstops rebinding here — and an accepted host is added to the
            set. Passing one is what makes a chain that revisits a host cost
            a single resolution rather than one per hop, which matters
            because the cluster has no caching resolver and ``ndots`` search
            expansion multiplies every external-name lookup. Deliberately
            per-fetch and never service-wide: "public a moment ago, in this
            chain" is a far narrower claim than "public once".

        Raises
        ------
        InvalidInventoryUrlError
            Raised if the URL cannot be parsed, uses a non-``https``
            scheme, or its host resolves to a non-public address.
        _UpstreamFetchError
            Raised if the host cannot be resolved at all, whether the
            resolver failed or answered with no addresses.
        """
        parts = self._parse_url(url)
        if parts.scheme != "https":
            self._reject_url(
                url, f"URL scheme must be 'https', not {parts.scheme!r}"
            )
        host = parts.host
        if not host:
            self._reject_url(url, "URL has no host to validate")
        if validated_hosts is not None and host in validated_hosts:
            # Already resolved and accepted earlier in this same fetch;
            # re-resolving it would answer the same question again.
            return

        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            # Not an IP literal: resolve the hostname to its addresses.
            try:
                resolved = list(await self._resolve_host(host))
            except (OSError, UnicodeError) as exc:
                # getaddrinfo reports a retired hostname or a transient DNS
                # failure as socket.gaierror (an OSError) and a host label
                # IDNA cannot encode as UnicodeEncodeError. Neither is an
                # httpx.HTTPError, so left to propagate they escape both
                # fetch paths' handlers; failing here keeps every
                # resolution failure inside the upstream-failure taxonomy,
                # which the redirect-hop wrapper then re-describes as a
                # refused hop.
                self._fail_resolution(
                    url, f"Host {host!r} could not be resolved: {exc}"
                )
            addresses = [ipaddress.ip_address(a) for a in resolved]
        if not addresses:
            # Reachable only from the resolver, never from the IP-literal
            # branch above, and an empty answer is the same absence of an
            # answer a resolver error is.
            self._fail_resolution(
                url, f"Host {host!r} did not resolve to any address"
            )
        for address in addresses:
            # For IPv4-mapped IPv6 addresses, guard the embedded IPv4
            # address rather than the IPv6 wrapper.
            candidate = (
                address.ipv4_mapped
                if isinstance(address, ipaddress.IPv6Address)
                and address.ipv4_mapped is not None
                else address
            )
            if not candidate.is_global:
                self._reject_url(
                    url,
                    f"Host {host!r} resolves to the non-public address"
                    f" {address}",
                )
        if validated_hosts is not None:
            validated_hosts.add(host)

    async def _guard_redirect_url(
        self,
        url: str,
        *,
        requested_url: str,
        validated_hosts: set[str],
    ) -> None:
        """Run the SSRF guard on a redirect hop's target, inside the budget.

        Same check as `_guard_url`, but a rejection is re-raised as an
        `_UpstreamFetchError` rather than an `InvalidInventoryUrlError`:
        the requested URL was valid and upstream chose this hop, so it is
        an upstream failure (502, negatively cached) rather than a bad
        client request (400).

        The rejection carries `_UNSAFE_REDIRECT_DETAIL`, which says nothing
        about the hop or why it was refused, so the reason is logged here
        instead — in one record naming both halves, since the served detail
        and the negative-cache row now name only the requested URL while the
        guard's own rejection log names only the hop.

        The guard's own resolver has no timeout, so the lookup is bounded by
        the enclosing `_fetch_budget`'s cancellation: a chain can point at as
        many hosts as the hop cap allows, and a hop whose resolution hangs
        would otherwise stall the fetch outside every bound it has.

        Parameters
        ----------
        url
            The redirect hop's target URL.
        requested_url
            The originally requested inventory URL, for the rejection log.
        validated_hosts
            The fetch's set of already-accepted hosts, passed through to
            `_guard_url` so a hop back to a host this chain already resolved
            skips the repeat lookup.

        Raises
        ------
        _UpstreamFetchError
            Raised if the hop target uses a non-``https`` scheme, its host
            cannot be resolved, or its host resolves to a non-public
            address.
        """
        try:
            await self._guard_url(url, validated_hosts=validated_hosts)
        except (InvalidInventoryUrlError, _UpstreamFetchError) as exc:
            # Exactly the two classes `_guard_url` raises: a rejection of
            # the hop for what it is, and the absence of an answer about
            # its host. Both are re-described here as one refused hop, so
            # an unresolvable target upstream chose is never reported as
            # though the client's own URL had failed to resolve. `reason`
            # is the scrubbed detail by this point; what the resolver
            # actually said is in `_fail_resolution`'s own earlier record,
            # against this hop's URL.
            self._logger.warning(
                "Rejected an intersphinx inventory redirect hop",
                url=requested_url,
                hop_url=url,
                reason=str(exc),
            )
            raise _UpstreamFetchError(_UNSAFE_REDIRECT_DETAIL) from exc

    def _reject_url(self, url: str, reason: str) -> NoReturn:
        """Log a guard rejection and raise ``InvalidInventoryUrlError``."""
        self._logger.warning(
            "Rejected intersphinx inventory URL by SSRF guard",
            url=url,
            reason=reason,
        )
        raise InvalidInventoryUrlError(reason)

    def _fail_resolution(self, url: str, reason: str) -> NoReturn:
        """Log a resolution failure and raise ``_UpstreamFetchError``.

        The scrub lives here at the raise site, unlike the refresh path's
        scrub of a guard *rejection*, which lives at that path's error
        boundary. The difference is who reads each. A rejection is shared
        with the request path's 400, which keeps the specific reason for
        the client that chose the URL, so only the refresh side of it can
        be scrubbed. A resolution failure has no 400 path left: every path
        it reaches ends in a stored, replayed detail, so the exception
        carries the generic one from birth and every reader of it is
        scrub-safe without having to remember to be.
        """
        self._logger.warning(
            "Could not resolve an intersphinx inventory host",
            url=url,
            reason=reason,
        )
        raise _UpstreamFetchError(_UNRESOLVABLE_HOST_DETAIL)


def _first_location(headers: httpx.Headers) -> str | None:
    """Return the first ``Location`` header value, or None if there is none.

    ``headers.get("Location")`` concatenates repeated headers with ``", "``,
    per RFC 9110 §5.2, which is right for a list-valued field and wrong for
    this one: ``Location`` is singular (RFC 9110 §10.2.2), so the
    concatenation is not a value the origin ever sent. Joined against the hop
    that sent it, the pair percent-encodes into one URL naming neither target,
    which keeps the origin's host often enough to pass the SSRF guard, be
    fetched, and 404 — negatively caching a working inventory and 502ing
    every documenteer build for the negative-TTL window. Taking the first
    value follows a target the origin actually named.

    The link checker's hop loop mirrors this rather than sharing it through
    `ook.domain.redirects`, for the same reason `_join_redirect_url` is
    mirrored: that module holds redirect *policy* and would have to take an
    httpx dependency into the domain layer to hold a helper over
    ``httpx.Headers``.
    """
    values = headers.get_list("Location")
    return values[0] if values else None


def _join_redirect_url(current_url: str, location: str) -> str:
    """Resolve a redirect's ``Location`` against the hop that sent it.

    Any fragment on the joined target is dropped. A fragment identifies a
    place to look inside a document, never part of an inventory's identity
    as a resource, and it is not sent on the wire in any case — so it
    changes nothing about the hop that gets fetched, while the joined URL
    *is* what the chain records as its terminal and serves back as the
    ``X-Ook-Inventory-Permanent-Redirect`` header. Stripping it here, at the
    one place a hop target is minted, keeps a doc author from being told to
    paste ``objects.inv#moved`` into ``intersphinx_mapping``.

    httpx builds a redirect request for every 3xx carrying a ``Location``,
    even with ``follow_redirects=False``, and reports a ``Location`` it
    cannot parse as an ``httpx.RemoteProtocolError`` before the response is
    ever returned — so in practice this join only sees targets httpx has
    already accepted. This conversion is the backstop for the residue (a
    relative target that overflows the URL length limit only once joined)
    and for that httpx behavior changing: ``httpx.InvalidURL`` is not an
    ``httpx.HTTPError``, so an unconverted join failure would escape both
    fetch paths' handlers instead of being negatively cached as an upstream
    failure.

    Raises
    ------
    _UpstreamFetchError
        Raised when the ``Location`` cannot be resolved to a valid URL.
    """
    try:
        return str(
            httpx.URL(current_url).join(location).copy_with(fragment=None)
        )
    except (httpx.InvalidURL, UnicodeError) as exc:
        raise _UpstreamFetchError(_malformed_redirect_detail(exc)) from exc


def _malformed_redirect_detail(error: Exception) -> str:
    """Format the client-facing detail for an unusable redirect target.

    Shared by `_join_redirect_url` and by `_fetch_inventory`'s conversion of
    the same failure raised from inside httpx, so which of the two joins
    tripped is not something a client (or a negative-cache row) can tell
    apart.
    """
    return f"Upstream redirected the inventory to a malformed URL: {error}"


async def _default_resolve_host(host: str) -> Sequence[str]:
    """Resolve a hostname to IP address strings with getaddrinfo."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


_GENERIC_UPSTREAM_ERROR = "Upstream fetch of the inventory failed"
"""Fallback detail when a negative-cache row has no stored error message."""


def _format_size_cap(max_content_size: int) -> str:
    """Render the size cap for an error detail (MB when a clean multiple)."""
    mebibyte = 1024 * 1024
    if max_content_size % mebibyte == 0:
        return f"{max_content_size // mebibyte} MB"
    return f"{max_content_size} bytes"


def _describe_upstream_error(error: httpx.HTTPError) -> str:
    """Summarize an upstream fetch failure for the client and the cache.

    The message is safe to return to the client and to store as the
    negative-cache row's error detail. A failure this service raised itself
    already carries such a message and is passed through; the branches
    below describe the transport's own failures, which do not.
    """
    if isinstance(error, _UpstreamFetchError):
        return str(error)
    if isinstance(error, httpx.HTTPStatusError):
        return (
            "Upstream returned HTTP "
            f"{error.response.status_code} for the inventory"
        )
    if isinstance(error, httpx.TimeoutException):
        return "Upstream request for the inventory timed out"
    return _GENERIC_UPSTREAM_ERROR
