"""HTTP checker that resolves a URL to a link-check outcome."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
from httpx import AsyncClient
from structlog.stdlib import BoundLogger

from ook.domain.linkcheck import (
    CheckResult,
    LinkCheckOutcome,
    is_supported_url,
)

__all__ = ["HostResolver", "UrlChecker"]

_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
"""Browser-like Accept header so bot-protection heuristics treating the
default automation Accept as suspicious do not block link-check requests.
"""

HostResolver = Callable[[str], Awaitable[Sequence[str]]]
"""Type of a callable resolving a hostname to IP address strings."""

_SUCCESS_CODES = range(200, 300)
"""HTTP status codes counted as a successful resolution."""

_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
"""HTTP status codes followed as redirects."""

_PERMANENT_REDIRECT_CODES = frozenset({301, 308})
"""Redirect status codes indicating the source should be updated."""

_MAX_REDIRECTS = 20
"""Maximum number of redirect hops followed before giving up."""

_RATE_LIMITED_CODE = 429
"""HTTP status code signalling the client is being rate limited."""

_TRANSIENT_CODES = frozenset({429, 503})
"""HTTP status codes treated as transient server conditions.

Following Sphinx linkcheck, a persistent 429 (rate limit) or a 503
(server-side outage) says nothing about whether the link is broken, so
these outcomes are inconclusive: they never escalate the failing→broken
ladder.
"""

_MAX_RETRY_AFTER_SECONDS = 60.0
"""Ceiling on an honored ``Retry-After`` delay, so a hostile or mistaken
header cannot stall a check indefinitely. The effective cap is further
bounded by the per-request timeout budget.
"""

_DIAGNOSTIC_HEADERS = ("server", "cf-mitigated", "cf-ray")
"""Terminal-response headers captured into failure error details, in the
order they are rendered. These surface whether a 4xx/5xx block came from an
edge mitigation (e.g. a Cloudflare challenge) rather than the origin.
"""


class _TooManyRedirectsError(Exception):
    """The redirect chain exceeded the maximum number of hops."""


class _UnsupportedUrlError(Exception):
    """The URL cannot be checked (bad scheme, malformed, or blocked
    host).
    """


@dataclass(slots=True)
class _FetchResult:
    """The terminal response of a fetch, with any redirect hops."""

    status_code: int
    """HTTP status code of the final response."""

    final_url: str
    """The URL that produced the final response."""

    redirect_hops: list[int] = field(default_factory=list)
    """Status codes of the redirect responses followed, in order."""

    diagnostic_headers: dict[str, str] = field(default_factory=dict)
    """Select diagnostic headers from the terminal response, keyed by
    lowercased header name, present only when the response carried them.
    """

    retry_after: str | None = None
    """The raw ``Retry-After`` header of the terminal response, if any."""

    @property
    def is_success(self) -> bool:
        return self.status_code in _SUCCESS_CODES

    @property
    def is_transient(self) -> bool:
        """Whether this is a transient server condition (429/503).

        A persistent rate limit or a server-side outage is inconclusive:
        it does not confirm the link is broken.
        """
        return self.status_code in _TRANSIENT_CODES

    @property
    def is_bot_blocked(self) -> bool:
        """Whether this is a 403 carrying Cloudflare bot-mitigation markers.

        True only for a 403 whose terminal response has ``server:
        cloudflare`` (case-insensitive) and/or a ``cf-mitigated`` header,
        indicating the block came from edge bot protection rather than a
        genuine origin authorization failure. A bare ``cf-ray`` (present
        on all Cloudflare responses) is not a mitigation marker and does
        not qualify.
        """
        if self.status_code != 403:
            return False
        server = self.diagnostic_headers.get("server", "")
        return (
            server.casefold() == "cloudflare"
            or "cf-mitigated" in self.diagnostic_headers
        )

    @property
    def redirect_status_code(self) -> int | None:
        """The redirect status code characterizing the chain.

        A chain is permanent only if every hop is permanent (301/308):
        a source URL cannot safely be updated past a temporary hop. For
        mixed chains this is the first temporary hop's status code.
        """
        if not self.redirect_hops:
            return None
        temporary = [
            code
            for code in self.redirect_hops
            if code not in _PERMANENT_REDIRECT_CODES
        ]
        return temporary[0] if temporary else self.redirect_hops[0]


class UrlChecker:
    """A deep module that takes a URL and returns a check outcome.

    The checker performs a HEAD request with a GET fallback on the
    shared HTTP client, captures redirects (final location and
    permanence), guards against SSRF (http/https schemes only; hostnames
    resolving to private, link-local, or loopback addresses are never
    fetched), enforces a global concurrency cap across all checks made
    through this instance, and spaces requests to the same host by a
    politeness interval.

    To close a DNS-rebinding TOCTOU gap, each request connects to the
    exact address the SSRF guard validated: the guard resolves and
    validates the host, and the outbound socket is pinned to that
    address while the original hostname is preserved for the ``Host``
    header and for TLS SNI plus certificate verification.

    Parameters
    ----------
    http_client
        The shared HTTP client used to make requests.
    logger
        A logger for check diagnostics.
    request_timeout
        Total timeout applied to each HTTP request.
    max_concurrency
        Maximum number of concurrent HTTP requests across all hosts.
    host_interval
        Minimum politeness interval between requests to the same host.
    user_agent
        User-Agent header sent on every request (HEAD, GET fallback, and
        redirect hops).
    resolve_host
        Hostname resolver used by the SSRF guard, mainly injectable for
        testing. Defaults to asyncio's ``getaddrinfo``. The address it
        returns is both validated by the guard and pinned as the socket
        connection target, so the request connects to the exact address
        that was checked.
    """

    def __init__(
        self,
        *,
        http_client: AsyncClient,
        logger: BoundLogger,
        request_timeout: timedelta = timedelta(seconds=30),
        max_concurrency: int = 10,
        host_interval: timedelta = timedelta(seconds=1),
        user_agent: str,
        resolve_host: HostResolver | None = None,
    ) -> None:
        self._http_client = http_client
        self._logger = logger
        self._timeout_seconds = request_timeout.total_seconds()
        self._host_interval = host_interval.total_seconds()
        self._user_agent = user_agent
        self._resolve_host = resolve_host or _default_resolve_host
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._host_locks: defaultdict[str, asyncio.Lock] = defaultdict(
            asyncio.Lock
        )
        self._host_next_time: dict[str, float] = {}

    async def check(self, url: str) -> LinkCheckOutcome:
        """Check a URL and report the outcome.

        Parameters
        ----------
        url
            The URL to check.

        Returns
        -------
        LinkCheckOutcome
            The outcome of the check, suitable as input to the domain
            status-transition engine.
        """
        try:
            pinned = await self._resolve_and_validate(url)
            result = await self._head_then_get(url, pinned)
            if result.status_code == _RATE_LIMITED_CODE:
                # A 429 is inconclusive, but honor a Retry-After delay with
                # a single in-run retry before giving up on it.
                result = await self._retry_after_once(url, pinned, result)
        except _UnsupportedUrlError as e:
            return self._unsupported_outcome(str(e))
        except socket.gaierror as e:
            return self._failure_outcome(f"DNS resolution failed: {e}")
        except TimeoutError, httpx.TimeoutException:
            return self._failure_outcome("Request timed out")
        except _TooManyRedirectsError:
            return self._failure_outcome(
                f"Exceeded {_MAX_REDIRECTS} redirects"
            )
        except httpx.HTTPError as e:
            return self._failure_outcome(_describe_error(e))
        return self._response_outcome(result)

    async def _head_then_get(self, url: str, pinned: str) -> _FetchResult:
        """Fetch with HEAD, falling back to GET when the response
        suggests the server mishandles HEAD (error status or dropped
        connection).

        Timeouts propagate immediately without a GET fallback: a fresh
        GET after a timed-out HEAD would double the worst-case wait per
        URL for a server that is most likely equally slow either way.

        Parameters
        ----------
        url
            The hostname URL to fetch.
        pinned
            The guard-validated address to connect to for the first hop.
        """
        try:
            result = await self._fetch(url, "HEAD", pinned)
            if result.is_success:
                return result
        except TimeoutError, httpx.TimeoutException:
            raise
        except httpx.HTTPError as e:
            self._logger.debug(
                "HEAD request failed; falling back to GET",
                url=url,
                error=str(e),
            )
        return await self._fetch(url, "GET", pinned)

    async def _retry_after_once(
        self, url: str, pinned: str, result: _FetchResult
    ) -> _FetchResult:
        """Retry a rate-limited fetch once after honoring ``Retry-After``.

        If the terminal 429 response carries a parseable ``Retry-After``
        delay, wait for it (capped) and re-run the HEAD/GET fetch a single
        time; the retry's result replaces the original. Without a usable
        delay there is nothing to honor, so the original 429 result stands
        and the caller reports it as an inconclusive transient outcome.

        Parameters
        ----------
        url
            The hostname URL to re-fetch.
        pinned
            The guard-validated address to connect to for the first hop.
        result
            The rate-limited terminal result of the first attempt.
        """
        delay = self._retry_after_delay(result.retry_after)
        if delay is None:
            return result
        await asyncio.sleep(delay)
        return await self._head_then_get(url, pinned)

    def _retry_after_delay(self, value: str | None) -> float | None:
        """Compute the delay to honor from a ``Retry-After`` header value.

        Returns the delay in seconds, clamped to at least zero and capped
        by both the 60 s ceiling and the per-request timeout budget, or
        None if the header is absent or not parseable.
        """
        if value is None:
            return None
        seconds = _parse_retry_after(value)
        if seconds is None:
            return None
        cap = min(_MAX_RETRY_AFTER_SECONDS, self._timeout_seconds)
        return min(max(seconds, 0.0), cap)

    async def _fetch(self, url: str, method: str, pinned: str) -> _FetchResult:
        """Fetch a URL, following redirects manually so that every hop
        passes the SSRF guard, and return the terminal response.

        Parameters
        ----------
        url
            The hostname URL to fetch.
        method
            The HTTP method for each hop.
        pinned
            The guard-validated address to connect to for the first hop.
        """
        current_url = url
        current_pinned = pinned
        hops: list[int] = []
        for _ in range(_MAX_REDIRECTS + 1):
            response = await self._send(method, current_url, current_pinned)
            location = response.headers.get("Location")
            if response.status_code in _REDIRECT_CODES and location:
                hops.append(response.status_code)
                current_url = str(httpx.URL(current_url).join(location))
                # The caller guards the original URL; guard each redirect
                # target before it is fetched, pinning the next hop's
                # connection to the address the guard validated.
                current_pinned = await self._resolve_and_validate(current_url)
                continue
            return _FetchResult(
                status_code=response.status_code,
                final_url=current_url,
                redirect_hops=hops,
                diagnostic_headers={
                    name: response.headers[name]
                    for name in _DIAGNOSTIC_HEADERS
                    if name in response.headers
                },
                retry_after=response.headers.get("Retry-After"),
            )
        raise _TooManyRedirectsError

    async def _send(
        self, method: str, url: str, pinned: str
    ) -> httpx.Response:
        """Make one HTTP request under the concurrency cap and the
        per-host politeness interval, connecting to the guard-validated
        address while preserving the hostname for Host and TLS.

        Parameters
        ----------
        method
            The HTTP method.
        url
            The hostname URL. Politeness is keyed on this hostname, not
            the pinned IP, so per-host spacing stays correct.
        pinned
            The guard-validated address to connect to.
        """
        # Key politeness on the original hostname, not the pinned IP, so
        # spacing tracks the logical host rather than the resolved
        # address.
        host = urlsplit(url).hostname or ""
        await self._wait_for_host_slot(host)
        # Pin the socket to the guard-validated address while keeping the
        # original hostname for routing and TLS: the URL host becomes the
        # validated IP (so the socket connects there), the ``Host`` header
        # preserves virtual-host routing, and the ``sni_hostname``
        # extension drives both TLS SNI and certificate hostname
        # verification against the original hostname. A DNS-rebinding
        # answer at httpx-connect time therefore cannot redirect the
        # socket to an internal address. Link-check targets carry no
        # proxy or userinfo, so pinning the first validated address
        # (trading httpx's multi-address failover for SSRF safety) is
        # acceptable.
        original = httpx.URL(url)
        sni_host = original.host  # original hostname (bare, unbracketed)
        authority = original.netloc.decode("ascii")  # host[:port], v6 in []
        request_url = original.copy_with(host=pinned)
        async with self._semaphore:
            return await self._http_client.request(
                method,
                request_url,
                headers={
                    "Host": authority,
                    "User-Agent": self._user_agent,
                    "Accept": _ACCEPT,
                },
                extensions={"sni_hostname": sni_host},
                follow_redirects=False,
                timeout=self._timeout_seconds,
            )

    async def _wait_for_host_slot(self, host: str) -> None:
        """Sleep until this request's slot on the host's politeness
        schedule.

        A per-host lock serializes schedule claims so that concurrent
        requests to the same host each claim a slot one politeness
        interval after the previous one.
        """
        if self._host_interval <= 0:
            return
        loop = asyncio.get_running_loop()
        async with self._host_locks[host]:
            now = loop.time()
            slot = max(self._host_next_time.get(host, now), now)
            self._host_next_time[host] = slot + self._host_interval
        delay = slot - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)

    def _response_outcome(self, result: _FetchResult) -> LinkCheckOutcome:
        """Build the check outcome for a terminal HTTP response."""
        redirect_status_code = result.redirect_status_code
        redirect_url = (
            result.final_url if redirect_status_code is not None else None
        )
        if result.is_success:
            return LinkCheckOutcome(
                checked_at=datetime.now(tz=UTC),
                result=CheckResult.success,
                status_code=result.status_code,
                redirect_status_code=redirect_status_code,
                redirect_url=redirect_url,
            )
        error = f"HTTP {result.status_code}"
        detail_parts: list[str] = []
        if result.is_bot_blocked:
            detail_parts.append("likely blocked by bot protection")
        elif result.status_code == _RATE_LIMITED_CODE:
            detail_parts.append("rate limited")
        elif result.is_transient:
            detail_parts.append("transient server error")
        detail_parts.extend(
            f"{name}={value}"
            for name, value in result.diagnostic_headers.items()
        )
        if detail_parts:
            error = f"{error} ({'; '.join(detail_parts)})"
        return LinkCheckOutcome(
            checked_at=datetime.now(tz=UTC),
            result=CheckResult.failure,
            status_code=result.status_code,
            redirect_status_code=redirect_status_code,
            redirect_url=redirect_url,
            error=error,
            is_bot_blocked=result.is_bot_blocked,
            is_transient=result.is_transient,
        )

    async def _resolve_and_validate(self, url: str) -> str:
        """Validate the URL against the SSRF guard and return the address
        the connection should be pinned to.

        The URL must be a well-formed http(s) URL whose host resolves
        only to globally-routable addresses. The returned address is the
        exact one validated here, so the caller can connect to it and
        close the DNS-rebinding TOCTOU gap.

        Returns
        -------
        str
            The validated address to connect to: the host itself when it
            is an IP literal, otherwise the first resolved address.

        Raises
        ------
        _UnsupportedUrlError
            Raised if the URL has an unsupported scheme, is malformed,
            or its host resolves to a non-public address.
        socket.gaierror
            Raised if the hostname cannot be resolved.
        """
        if not is_supported_url(url):
            raise _UnsupportedUrlError(
                f"URL is not a well-formed http(s) URL: {url!r}"
            )
        host = urlsplit(url).hostname
        if host is None:
            raise _UnsupportedUrlError(f"URL has no host: {url!r}")

        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            # Not an IP literal: resolve the hostname.
            resolved = list(await self._resolve_host(host))
            addresses = [ipaddress.ip_address(a) for a in resolved]
        else:
            resolved = [host]
        if not addresses:
            raise socket.gaierror(
                socket.EAI_NODATA, f"No addresses found for {host!r}"
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
                raise _UnsupportedUrlError(
                    f"Host {host!r} resolves to the non-public address"
                    f" {address}"
                )
        # Pin to the first validated address as resolved (for a literal,
        # the host itself). The socket connects to this exact address.
        return resolved[0]

    def _unsupported_outcome(self, error: str) -> LinkCheckOutcome:
        return LinkCheckOutcome(
            checked_at=datetime.now(tz=UTC),
            result=CheckResult.unsupported,
            error=error,
        )

    def _failure_outcome(self, error: str) -> LinkCheckOutcome:
        return LinkCheckOutcome(
            checked_at=datetime.now(tz=UTC),
            result=CheckResult.failure,
            error=error,
        )


def _parse_retry_after(value: str) -> float | None:
    """Parse a ``Retry-After`` header into a delay in seconds.

    Supports both forms defined by RFC 9110: a non-negative delta-seconds
    integer, or an HTTP-date (interpreted as a delay from now). Returns
    None if the value is neither.
    """
    value = value.strip()
    try:
        return float(int(value))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (parsed - datetime.now(tz=UTC)).total_seconds()


def _describe_error(error: Exception) -> str:
    """Format an exception as a short diagnostic string."""
    message = str(error)
    if message:
        return f"{type(error).__name__}: {message}"
    return type(error).__name__


async def _default_resolve_host(host: str) -> Sequence[str]:
    """Resolve a hostname to IP address strings with getaddrinfo."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]
