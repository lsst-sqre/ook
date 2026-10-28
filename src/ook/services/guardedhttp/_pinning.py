"""Pin a request's socket to the address the SSRF guard validated.

A caller validates a host with `resolve_and_validate` and then makes its
request inside `pinned_to`, which records host → validated address in a
context variable for the duration of that one request.
`PinningNetworkBackend`, the network backend under the guarded client's
connection pool, reads that mapping when httpcore opens a socket and dials
the validated address instead of re-resolving the hostname, which closes the
DNS-rebinding gap between the guard's lookup and the connect.

The rule this module exists to uphold: **no service rewrites a URL host to
an IP.** The request URL keeps its hostname, so TLS SNI and certificate
verification use the hostname, and httpcore's connection pool -- keyed on
``(scheme, host, port)`` -- reuses a connection only for the same hostname.
Rewriting the host to the pinned IP instead keys the pool by IP, and a CDN
serving several hostnames from one address (Fastly serves docs.python.org
and pypi.org that way) then gets one hostname's request on a connection
opened for another, answered with a 421 Misdirected Request.
"""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType

import httpcore
import httpx
import structlog
from httpcore._backends.auto import AutoBackend
from structlog.stdlib import BoundLogger

from ._guard import (
    HostGuardError,
    HostResolver,
    check_public,
    resolve_and_validate,
)

__all__ = [
    "GuardRefusedConnectError",
    "PinningNetworkBackend",
    "pinned_address",
    "pinned_to",
]

_NO_PINS: Mapping[str, str] = MappingProxyType({})
"""The pin mapping outside any `pinned_to` block."""

_pins: ContextVar[Mapping[str, str]] = ContextVar("ook_guardedhttp_pins")
"""Host → validated address for the request about to be made.

Keyed by `_host_key`, so a caller's hostname and the one httpcore hands
`PinningNetworkBackend.connect_tcp` agree however each is spelled.
"""


class GuardRefusedConnectError(httpcore.ConnectError):
    """`PinningNetworkBackend` refused to dial a host the SSRF guard
    rejected.

    Raised only on the fallback path, for a request made with no pinned
    address. httpx reports it to the caller as an ``httpx.ConnectError``
    carrying the same message, which names the rejection.
    """


@contextmanager
def pinned_to(host: str, address: str) -> Iterator[None]:
    """Pin connections to a host to a guard-validated address.

    Wrap a single request in this: any socket the guarded client opens to
    ``host`` inside the block dials ``address``. The request URL itself
    keeps the hostname; see the module docs for why that matters. Pins
    nest, so a block pinning one host keeps any outer block's pins for
    other hosts, and leaving the block restores the pins it was entered
    with.

    Parameters
    ----------
    host
        The request URL's host, spelled as the URL spells it.
    address
        The address `resolve_and_validate` returned for ``host``.

    Raises
    ------
    NonPublicHostError
        Raised if ``address`` is not a public address, so a caller that
        skipped the guard cannot pin a socket somewhere it must not go.
    ValueError
        Raised if ``address`` is not an IP literal, which would have the
        socket re-resolve a hostname and reopen the rebinding gap.
    """
    check_public(host, ipaddress.ip_address(address))
    pins = dict(_pins.get(_NO_PINS))
    pins[_host_key(host)] = address
    token = _pins.set(MappingProxyType(pins))
    try:
        yield
    finally:
        _pins.reset(token)


def pinned_address(host: str) -> str | None:
    """Return the address the current context pins a host to.

    Parameters
    ----------
    host
        The host to look up, however it is spelled.

    Returns
    -------
    str or None
        The pinned address, or None outside any `pinned_to` block for the
        host.
    """
    return _pins.get(_NO_PINS).get(_host_key(host))


class PinningNetworkBackend(httpcore.AsyncNetworkBackend):
    """An httpcore network backend that dials guard-validated addresses.

    ``connect_tcp`` dials the address `pinned_to` set for the host rather
    than the host itself. A request made with no pinned address fails
    closed: the backend runs `resolve_and_validate` on the host itself and
    dials the result, or refuses with `GuardRefusedConnectError`, so no
    connection through this backend is ever made unguarded. That fallback
    means a caller skipped its own pre-flight guard, so it is logged as a
    warning. Every other method delegates unchanged.

    Parameters
    ----------
    backend
        The backend that actually opens sockets. Defaults to httpcore's
        ``AutoBackend``, the one its connection pool uses by default;
        injectable for testing.
    resolve_host
        The resolver for the fallback guard. Defaults to
        `default_resolve_host`.
    logger
        Logger for the fallback warning.
    """

    def __init__(
        self,
        *,
        backend: httpcore.AsyncNetworkBackend | None = None,
        resolve_host: HostResolver | None = None,
        logger: BoundLogger | None = None,
    ) -> None:
        self._backend = backend if backend is not None else AutoBackend()
        self._resolve_host = resolve_host
        self._logger = logger or structlog.get_logger("ook")

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        address = pinned_address(host)
        if address is None:
            address = await self._validate_unpinned(host, timeout)
        return await self._backend.connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._backend.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)

    async def _validate_unpinned(
        self, host: str, timeout: float | None
    ) -> str:
        """Guard a host no caller pinned, returning the address to dial.

        The lookup is held to the connect timeout, since httpx's own
        timeout covers only the connect this precedes.

        Raises
        ------
        GuardRefusedConnectError
            Raised if the guard rejects the host.
        httpcore.ConnectTimeout
            Raised if the lookup outlasts the connect timeout.
        """
        self._logger.warning(
            "Guarded connection made without a pinned address; validating"
            " the host at connect time",
            host=host,
        )
        try:
            async with asyncio.timeout(timeout):
                return await resolve_and_validate(
                    host, resolve_host=self._resolve_host
                )
        except HostGuardError as exc:
            raise GuardRefusedConnectError(
                f"SSRF guard refused to connect: {exc}"
            ) from exc
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout(
                f"Timed out resolving {host!r} for the SSRF guard"
            ) from exc


def _host_key(host: str) -> str:
    """Normalize a host into the key `pinned_to` and the backend share.

    httpcore hands ``connect_tcp`` the URL's raw host: IDNA-encoded,
    lowercased (except IPv6 literals, which keep their case), and
    unbracketed. A caller holding a URL may spell the same host as
    `urllib.parse.urlsplit` does instead (Unicode, lowercased), or as a
    bracketed literal. Normalizing both sides keeps a pin from missing,
    which would only cost a duplicate lookup through the fail-closed
    fallback, but would cost it on every request.
    """
    bare = host.removeprefix("[").removesuffix("]")
    try:
        return str(ipaddress.ip_address(bare))
    except ValueError:
        pass
    if bare.isascii():
        return bare.lower()
    try:
        return httpx.URL(scheme="https", host=bare).raw_host.decode("ascii")
    except httpx.InvalidURL, UnicodeError:
        # httpx refuses the host outright, so no request can reach a
        # socket with it; any stable key will do.
        return bare.lower()
