"""Service for the intersphinx inventory cache."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import NoReturn
from urllib.parse import urlsplit

import httpx
from httpx import AsyncClient
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


class _InventoryTooLargeError(httpx.HTTPError):
    """An origin inventory response exceeded the configured size cap.

    Modeled as an ``httpx.HTTPError`` so an oversized body reuses the same
    upstream-failure plumbing as a 4xx/5xx or timeout: the cold-miss path
    catches it and negatively caches the failure, and the refresh path
    counts it as a per-inventory failure. Both paths therefore need no
    extra catch clause, only a branch in `_describe_upstream_error`.
    """


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
    origin: redirects are never followed (so the SSRF guard cannot be
    bypassed one hop at a time), each request carries an explicit timeout,
    and the response body is streamed under a size cap so an oversized
    inventory is abandoned rather than buffered into memory. An oversized
    response is treated as an upstream fetch failure.

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
        Per-request timeout applied to each upstream inventory fetch, on
        both the cold-miss and refresh paths.
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
        timeout, connection error) is logged and skipped; the stored copy is
        left untouched so it keeps serving stale, and the rest of the batch
        continues. This is the background counterpart to the request path:
        the request path never blocks on upstream because this job keeps the
        cache warm.

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
        refreshed = 0
        revalidated = 0
        failed = 0
        for inventory in due:
            try:
                was_revalidated = await self._refresh_one(inventory, now=now)
            except (httpx.HTTPError, InvalidInventoryUrlError) as exc:
                failed += 1
                self._logger.warning(
                    "Failed to refresh intersphinx inventory",
                    url=inventory.url,
                    cache_status="refresh-failure",
                    error=(
                        _describe_upstream_error(exc)
                        if isinstance(exc, httpx.HTTPError)
                        else str(exc)
                    ),
                )
                continue
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
        caller can log and skip it, leaving the stored copy untouched.
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
        response, content = await self._fetch_inventory(
            inventory.url, headers=headers
        )
        if response.status_code == 304:
            await self._inventory_store.upsert_inventory(
                replace(
                    inventory,
                    date_fetched=now,
                    last_fetch_status=InventoryFetchStatus.success,
                    last_fetch_error=None,
                )
            )
            self._logger.info(
                "Revalidated intersphinx inventory (304 Not Modified)",
                url=inventory.url,
                cache_status="revalidated",
            )
            return True
        response.raise_for_status()
        await self._inventory_store.upsert_inventory(
            replace(
                inventory,
                content=content,
                content_type=response.headers.get("Content-Type"),
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                date_fetched=now,
                last_fetch_status=InventoryFetchStatus.success,
                last_fetch_error=None,
            )
        )
        self._logger.info(
            "Refreshed intersphinx inventory (200 OK)",
            url=inventory.url,
            cache_status="refreshed",
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
            response, content = await self._fetch_inventory(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            await self._store_failure(url, error=exc)
        now = datetime.now(tz=UTC)
        inventory = IntersphinxInventory(
            url=url,
            content=content,
            content_type=response.headers.get("Content-Type"),
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            date_fetched=now,
            date_requested=now,
            last_fetch_status=InventoryFetchStatus.success,
            last_fetch_error=None,
        )
        await self._inventory_store.upsert_inventory(inventory)
        return inventory

    async def _fetch_inventory(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> tuple[httpx.Response, bytes | None]:
        """Fetch an origin inventory, bounding the body by the size cap.

        The request never follows redirects and carries the service's
        per-request timeout. The body is streamed so an oversized response
        is abandoned as soon as the cap is exceeded rather than fully
        buffered; a ``Content-Length`` already over the cap aborts before
        any body is read.

        Returns the response together with its body bytes, or ``None`` bytes
        for a ``304 Not Modified``, which carries no body and is not read so
        the caller can revalidate the stored copy in place.

        Raises
        ------
        _InventoryTooLargeError
            Raised when the response body, by its ``Content-Length`` or by
            its streamed size, exceeds the configured cap.
        httpx.HTTPError
            Propagated from the transport on a timeout or connection error.
        """
        async with self._http_client.stream(
            "GET",
            url,
            headers=headers or {},
            follow_redirects=False,
            timeout=self._request_timeout,
        ) as response:
            if response.status_code == 304:
                return response, None
            self._check_content_length(response)
            content = await self._read_capped_body(response)
            return response, content

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
        """Stream the response body, aborting once the cap is exceeded."""
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
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
            Raised if the URL uses a non-``https`` scheme or its host
            resolves to a non-public address.
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
            resolved = list(await self._resolve_host(host))
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

    def _reject_url(self, url: str, reason: str) -> NoReturn:
        """Log a guard rejection and raise ``InvalidInventoryUrlError``."""
        self._logger.warning(
            "Rejected intersphinx inventory URL by SSRF guard",
            url=url,
            reason=reason,
        )
        raise InvalidInventoryUrlError(reason)


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
    if isinstance(error, _InventoryTooLargeError):
        return str(error)
    if isinstance(error, httpx.HTTPStatusError):
        return (
            "Upstream returned HTTP "
            f"{error.response.status_code} for the inventory"
        )
    if isinstance(error, httpx.TimeoutException):
        return "Upstream request for the inventory timed out"
    return _GENERIC_UPSTREAM_ERROR
