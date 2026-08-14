"""Service for the intersphinx inventory cache."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import NoReturn
from urllib.parse import urlsplit

import httpx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.stdlib import BoundLogger

from ook.domain.intersphinx import IntersphinxInventory, InventoryFetchStatus
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

    failed: int
    """The number of inventories whose refresh failed (logged, skipped)."""


HostResolver = Callable[[str], Awaitable[Sequence[str]]]
"""Type of a callable resolving a hostname to IP address strings."""


_DEFAULT_MAX_CONTENT_SIZE = 50 * 1024 * 1024
"""Default cap, in bytes, on an origin inventory response body (50 MB)."""

_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
"""HTTP status codes followed as redirects when fetching an inventory."""

_PERMANENT_REDIRECT_CODES = frozenset({301, 308})
"""Redirect status codes meaning the requested URL itself has moved.

Matches the link-check URL checker's set so both of Ook's fetch paths draw
the permanent/temporary line identically.
"""

_MAX_REDIRECTS = 20
"""Maximum number of redirect hops followed before giving up.

Matches the link-check URL checker's cap so both of Ook's manually-followed
fetch paths bound a redirect chain identically.
"""


class _InventoryTooLargeError(httpx.HTTPError):
    """An origin inventory response exceeded the configured size cap.

    Modeled as an ``httpx.HTTPError`` so an oversized body reuses the same
    upstream-failure plumbing as a 4xx/5xx or timeout: the cold-miss path
    catches it and negatively caches the failure, and the refresh path
    counts it as a per-inventory failure. Both paths therefore need no
    extra catch clause, only a branch in `_describe_upstream_error`.
    """


class _TooManyRedirectsError(httpx.HTTPError):
    """An origin inventory's redirect chain exceeded the hop cap.

    An ``httpx.HTTPError`` for the same reason as `_InventoryTooLargeError`:
    the client's URL was fine and upstream misbehaved, so this reuses the
    existing upstream-failure plumbing and surfaces as a 502.
    """


class _FetchDeadlineExceededError(httpx.HTTPError):
    """An origin inventory fetch outlasted its whole-chain time budget.

    The per-request timeout bounds one hop, not the chain: an origin that
    answers each redirect just inside that timeout could otherwise hold the
    fetch — and, on the cold-miss path, the request's open DB session — for
    the hop cap times the per-request timeout. An ``httpx.HTTPError`` for
    the same reason as `_TooManyRedirectsError`, so an exhausted budget
    lands in the existing upstream-failure plumbing: a negatively cached
    502 on the request path, a skipped inventory on the refresh path.
    """


class _InvalidRedirectError(httpx.HTTPError):
    """An origin's ``Location`` header could not be resolved to a URL.

    An ``httpx.HTTPError`` for the same reason as `_TooManyRedirectsError`:
    a ``Location`` the client never chose is upstream's misbehavior, so it
    surfaces as a 502 and is negatively cached rather than escaping as an
    unhandled error.
    """


class _UnsafeRedirectError(httpx.HTTPError):
    """A redirect hop's target failed the SSRF guard.

    Covers every way the guard can refuse a hop, including a host that will
    not resolve at all: the guard reports those as rejections so no
    resolver failure escapes as something other than an upstream failure.

    Distinct from `InvalidInventoryUrlError`, which reports a URL the
    *client* asked for and can fix. A hop chosen by upstream is upstream's
    misbehavior, so this is an ``httpx.HTTPError`` that surfaces as a 502
    and is negatively cached like any other upstream failure.
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

        A chain counts as permanent only when *every* hop is a 301 or 308: a
        single temporary hop means the terminal URL is not a stable
        replacement for the requested one, so the whole chain is temporary.
        """
        if not self.redirect_hops:
            return None
        return all(
            code in _PERMANENT_REDIRECT_CODES for code in self.redirect_hops
        )


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

    Every upstream fetch is hardened against a hostile or misbehaving
    origin: redirects are followed by hand, one guarded hop at a time and
    bounded by a hop cap (so the SSRF guard cannot be bypassed via an
    upstream ``Location`` and a redirect loop terminates), the whole chain
    runs against a single time budget so a slow origin cannot multiply the
    per-request timeout by the hop cap, and the terminal response body is
    streamed under a size cap so an oversized inventory is abandoned rather
    than buffered into memory. An oversized response, an over-long chain, an
    exhausted time budget, an unusable ``Location``, and a hop the guard
    rejects — including one whose host will not resolve — are all treated as
    upstream fetch failures, so every way a chain can fail lands in the same
    negative-cache-and-502 treatment on the request path and the same
    skip-one-inventory treatment on the refresh path.

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
        and refresh paths. It bounds the fetch as a whole — every redirect
        hop, every hop target's guard resolution, and the terminal body read
        share it — so a redirect chain costs no more wall-clock time than a
        single non-redirecting fetch.
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
        ``If-Modified-Since``). A ``304 Not Modified`` keeps the stored
        content and bumps ``date_fetched``; a ``200`` replaces the content
        and validators. Inventories requested longer ago than the active
        window are skipped, not deleted — a new client request reactivates
        them via ``date_requested``.

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
            and failed.
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
        refreshed = 0
        revalidated = 0
        failed = 0
        for inventory in due:
            try:
                was_revalidated = await self._refresh_one(inventory, now=now)
            except (httpx.HTTPError, InvalidInventoryUrlError) as exc:
                # Discard this inventory's pending write and leave the stored
                # copy untouched so it keeps serving stale.
                await self._session.rollback()
                failed += 1
                detail = (
                    _describe_upstream_error(exc)
                    if isinstance(exc, httpx.HTTPError)
                    else str(exc)
                )
                # Record the failed attempt in its own transaction, so the
                # inventory backs off instead of heading the due list again
                # on the very next run.
                await self._inventory_store.update_refresh_failure(
                    inventory.url, now=now, error=detail
                )
                await self._session.commit()
                self._logger.warning(
                    "Failed to refresh intersphinx inventory",
                    url=inventory.url,
                    cache_status="refresh-failure",
                    error=detail,
                )
                continue
            # Commit this inventory's outcome immediately so a later crash in
            # the batch cannot lose it and no transaction spans the next fetch.
            await self._session.commit()
            if was_revalidated:
                revalidated += 1
            else:
                refreshed += 1
        summary = IntersphinxRefreshSummary(
            considered=len(due),
            refreshed=refreshed,
            revalidated=revalidated,
            failed=failed,
        )
        self._logger.info(
            "Completed intersphinx inventory refresh",
            considered=summary.considered,
            refreshed=summary.refreshed,
            revalidated=summary.revalidated,
            failed=summary.failed,
        )
        return summary

    async def _refresh_one(
        self, inventory: IntersphinxInventory, *, now: datetime
    ) -> bool:
        """Revalidate one cached inventory with a conditional GET.

        Returns True when a ``304`` revalidated the stored copy in place and
        False when a ``200`` replaced its content. Raises on a guard
        rejection, an upstream failure, or an oversized response so the
        caller can log the failure, record its backoff, and skip to the next
        inventory, leaving the stored copy untouched.
        """
        # Re-guard the stored URL before fetching: it passed the guard when
        # first cached, but DNS can rebind a once-public host to a private
        # address, so the cheap re-check preserves the SSRF invariant.
        await self._guard_url(inventory.url)
        headers: dict[str, str] = {}
        if inventory.etag is not None:
            headers["If-None-Match"] = inventory.etag
        if inventory.last_modified is not None:
            headers["If-Modified-Since"] = inventory.last_modified
        fetch = await self._fetch_inventory(inventory.url, headers=headers)
        response = fetch.response
        if response.status_code == 304:
            # Write only the refresh-outcome columns so a client request that
            # bumped date_requested since the due-list read is not reverted.
            await self._inventory_store.update_refresh_outcome(
                replace(
                    inventory,
                    date_fetched=now,
                    last_fetch_status=InventoryFetchStatus.success,
                    last_fetch_error=None,
                    # A 304 says the content is unchanged, which says
                    # nothing about the chain: record the one this
                    # revalidation walked rather than carrying the stored
                    # one forward.
                    resolved_url=fetch.resolved_url,
                    resolved_redirect_permanent=(
                        fetch.resolved_redirect_permanent
                    ),
                    # A revalidated inventory is healthy again: drop any
                    # backoff an earlier failure left on it.
                    date_refresh_failed=None,
                )
            )
            self._logger.info(
                "Revalidated intersphinx inventory (304 Not Modified)",
                url=inventory.url,
                cache_status="revalidated",
                final_url=fetch.final_url,
                redirect_hops=len(fetch.redirect_hops),
            )
            return True
        response.raise_for_status()
        await self._inventory_store.update_refresh_outcome(
            replace(
                inventory,
                content=fetch.content,
                content_type=response.headers.get("Content-Type"),
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                date_fetched=now,
                last_fetch_status=InventoryFetchStatus.success,
                last_fetch_error=None,
                resolved_url=fetch.resolved_url,
                resolved_redirect_permanent=(
                    fetch.resolved_redirect_permanent
                ),
                # A refreshed inventory is healthy again: drop any backoff an
                # earlier failure left on it.
                date_refresh_failed=None,
            )
        )
        self._logger.info(
            "Refreshed intersphinx inventory (200 OK)",
            url=inventory.url,
            cache_status="refreshed",
            final_url=fetch.final_url,
            redirect_hops=len(fetch.redirect_hops),
        )
        return False

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
        await self._guard_url(url)
        self._logger.info(
            "Fetching intersphinx inventory on cache miss", url=url
        )
        try:
            fetch = await self._fetch_inventory(url)
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
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> _InventoryFetch:
        """Fetch an origin inventory, following redirects under the guard.

        Redirects are followed by hand rather than by httpx, one hop at a
        time, so the SSRF guard runs against every hop target before it is
        fetched and cannot be bypassed by an upstream ``Location``. A
        relative ``Location`` is resolved against the hop that sent it, not
        against the originally requested URL. The chain is bounded by
        `_MAX_REDIRECTS` hops so a redirect loop terminates.

        Only the terminal response's body is read: a redirect hop's body is
        discarded unread and never counted against the size cap, and both
        the ``Content-Length`` pre-check and the streamed-size cap apply to
        the terminal response alone. The terminal body is streamed so an
        oversized response is abandoned as soon as the cap is exceeded
        rather than fully buffered.

        The conditional-request headers, if any, are re-sent on every hop so
        a chain ending in a ``304 Not Modified`` still revalidates.

        The whole chain runs against a single time budget of
        ``request_timeout``, taken from the monotonic clock when the fetch
        starts: each hop's request, each hop target's guard resolution, and
        the terminal body read get only the time left in that budget, and
        the budget is re-checked before each of them. The per-request
        timeout alone would bound one hop rather than the chain, letting an
        origin that dribbles each redirect just inside it stretch a fetch to
        the hop cap times that timeout — on the cold-miss path, with the
        request's DB session held open the whole time.

        Raises
        ------
        _FetchDeadlineExceededError
            Raised when the chain outlasts the whole-fetch time budget.
        _InventoryTooLargeError
            Raised when the terminal response body, by its
            ``Content-Length`` or by its streamed size, exceeds the
            configured cap.
        _InvalidRedirectError
            Raised when a hop's ``Location`` cannot be resolved to a URL.
        _TooManyRedirectsError
            Raised when the chain exceeds `_MAX_REDIRECTS` hops.
        _UnsafeRedirectError
            Raised when a redirect hop's target fails the SSRF guard,
            including when its host cannot be resolved.
        httpx.HTTPError
            Propagated from the transport on a timeout or connection error.
        """
        deadline = time.monotonic() + self._request_timeout
        current_url = url
        hops: list[int] = []
        while True:
            async with self._http_client.stream(
                "GET",
                current_url,
                headers=headers or {},
                follow_redirects=False,
                timeout=self._remaining_budget(deadline),
            ) as response:
                location = response.headers.get("Location")
                if response.status_code not in _REDIRECT_CODES or not location:
                    if response.status_code == 304:
                        return _InventoryFetch(
                            response, None, current_url, hops
                        )
                    self._check_content_length(response)
                    content = await self._read_capped_body(
                        response, deadline=deadline
                    )
                    return _InventoryFetch(
                        response, content, current_url, hops
                    )
                if len(hops) >= _MAX_REDIRECTS:
                    # Give up on the hop count alone, before joining or
                    # guarding a target this fetch will never request, so
                    # the reported failure never depends on a URL already
                    # ruled out.
                    raise _TooManyRedirectsError(
                        f"Exceeded {_MAX_REDIRECTS} redirects"
                    )
                hops.append(response.status_code)
                next_url = _join_redirect_url(current_url, location)
            # Guard outside the stream context so the hop's connection is
            # released — and its body left unread — before the guard's DNS
            # lookup and the next hop's request.
            await self._guard_redirect_url(next_url, deadline=deadline)
            current_url = next_url

    def _remaining_budget(self, deadline: float) -> float:
        """Return the seconds left in the whole-fetch budget.

        Raises
        ------
        _FetchDeadlineExceededError
            Raised when the budget is already spent, so every caller both
            bounds the operation it is about to start and refuses to start
            it at all once there is no time left.
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise self._deadline_error()
        return remaining

    def _deadline_error(self) -> _FetchDeadlineExceededError:
        """Build the spent-budget error carrying the configured budget."""
        return _FetchDeadlineExceededError(
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

    async def _read_capped_body(
        self, response: httpx.Response, *, deadline: float
    ) -> bytes:
        """Stream the body, aborting on the size cap or the time budget.

        The request's read timeout bounds the wait for one chunk, not the
        whole body, so a body dribbled out a byte at a time would otherwise
        outlast the budget however small each gap is. Checking the budget
        per chunk keeps the terminal read inside it.
        """
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            self._remaining_budget(deadline)
            total += len(chunk)
            if total > self._max_content_size:
                raise self._too_large_error()
            chunks.append(chunk)
        return b"".join(chunks)

    def _too_large_error(self) -> _InventoryTooLargeError:
        """Build the oversized-response error carrying the configured cap."""
        return _InventoryTooLargeError(
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
                # A negative-cache row has no content and no resolved chain.
                resolved_url=None,
                resolved_redirect_permanent=None,
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

    async def _guard_url(self, url: str) -> None:
        """Reject a URL that must not be fetched from upstream.

        This SSRF guard runs before any upstream fetch: the URL must use
        ``https`` and its host must not resolve to a private, link-local,
        or loopback address. A rejected URL is never fetched and never
        stored.

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

        Raises
        ------
        InvalidInventoryUrlError
            Raised if the URL uses a non-``https`` scheme, its host cannot
            be resolved at all, or its host resolves to a non-public
            address.
        """
        parts = urlsplit(url)
        if parts.scheme != "https":
            self._reject_url(
                url, f"URL scheme must be 'https', not {parts.scheme!r}"
            )
        host = parts.hostname
        if not host:
            self._reject_url(url, "URL has no host to validate")

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
                # fetch paths' handlers; a rejection here keeps every
                # resolution failure inside the guard's own taxonomy, which
                # the redirect-hop wrapper then re-raises as an upstream
                # failure.
                self._reject_url(
                    url, f"Host {host!r} could not be resolved: {exc}"
                )
            addresses = [ipaddress.ip_address(a) for a in resolved]
        if not addresses:
            self._reject_url(
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

    async def _guard_redirect_url(self, url: str, *, deadline: float) -> None:
        """Run the SSRF guard on a redirect hop's target, inside the budget.

        Same check as `_guard_url`, but a rejection is re-raised as an
        `_UnsafeRedirectError` rather than an `InvalidInventoryUrlError`:
        the requested URL was valid and upstream chose this hop, so it is
        an upstream failure (502, negatively cached) rather than a bad
        client request (400).

        The guard's own resolver has no timeout, so the lookup is bounded by
        whatever is left of the fetch's time budget: a chain can point at as
        many hosts as the hop cap allows, and a hop whose resolution hangs
        would otherwise stall the fetch outside the budget entirely.

        Raises
        ------
        _FetchDeadlineExceededError
            Raised when the budget is spent before, or during, the guard's
            host resolution.
        _UnsafeRedirectError
            Raised if the hop target uses a non-``https`` scheme, its host
            cannot be resolved, or its host resolves to a non-public
            address.
        """
        try:
            async with asyncio.timeout(self._remaining_budget(deadline)):
                await self._guard_url(url)
        except TimeoutError as exc:
            raise self._deadline_error() from exc
        except InvalidInventoryUrlError as exc:
            raise _UnsafeRedirectError(
                f"Upstream redirected the inventory to a rejected URL: {exc}"
            ) from exc

    def _reject_url(self, url: str, reason: str) -> NoReturn:
        """Log a guard rejection and raise ``InvalidInventoryUrlError``."""
        self._logger.warning(
            "Rejected intersphinx inventory URL by SSRF guard",
            url=url,
            reason=reason,
        )
        raise InvalidInventoryUrlError(reason)


def _join_redirect_url(current_url: str, location: str) -> str:
    """Resolve a redirect's ``Location`` against the hop that sent it.

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
    _InvalidRedirectError
        Raised when the ``Location`` cannot be resolved to a valid URL.
    """
    try:
        return str(httpx.URL(current_url).join(location))
    except (httpx.InvalidURL, UnicodeError) as exc:
        raise _InvalidRedirectError(
            f"Upstream redirected the inventory to a malformed URL: {exc}"
        ) from exc


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
    negative-cache row's error detail.
    """
    if isinstance(
        error,
        _FetchDeadlineExceededError
        | _InventoryTooLargeError
        | _TooManyRedirectsError
        | _UnsafeRedirectError
        | _InvalidRedirectError,
    ):
        return str(error)
    if isinstance(error, httpx.HTTPStatusError):
        return (
            "Upstream returned HTTP "
            f"{error.response.status_code} for the inventory"
        )
    if isinstance(error, httpx.TimeoutException):
        return "Upstream request for the inventory timed out"
    return _GENERIC_UPSTREAM_ERROR
