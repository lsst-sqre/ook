"""The guarded httpx transport and client factory.

`GuardedTransport` is an ordinary httpx transport whose connection pool
opens its sockets through `PinningNetworkBackend`, so every request through
it connects to a guard-validated address. Requests keep their hostname --
**no service rewrites a URL host to an IP** -- so the pool stays keyed by
hostname, and TLS SNI and certificate verification use the hostname with no
``sni_hostname`` extension or ``Host`` override.

`create_guarded_client` refuses two groups of ``httpx.AsyncClient`` options
rather than forwarding them:

- ``transport``, ``mounts`` and ``proxy`` would route requests around the
  guarded transport.
- ``verify``, ``cert``, ``trust_env``, ``http1`` and ``http2`` only configure
  a transport that httpx builds itself, so a client given the guarded
  transport would silently ignore them. The guarded transport fixes its own
  TLS context (the defaults of ``httpx.create_ssl_context()``) and speaks
  HTTP/1.1 only, and the SSRF guard's TLS behavior is not configurable.

Options httpx applies at the client layer, such as ``timeout``, ``headers``
and ``follow_redirects``, pass through unchanged.
"""

from __future__ import annotations

from typing import Any

import httpcore
import httpx
from structlog.stdlib import BoundLogger

from ._guard import HostResolver
from ._pinning import PinningNetworkBackend

__all__ = ["DEFAULT_LIMITS", "GuardedTransport", "create_guarded_client"]

DEFAULT_LIMITS = httpx.Limits(
    max_connections=100, max_keepalive_connections=20
)
"""Pool limits when none are given: the ones a plain ``httpx.AsyncClient``
uses, so moving a caller onto the guarded client changes how its pool is
keyed and nothing else (keep-alive stays on, idle connections expire after
5 seconds).
"""

_FORBIDDEN_CLIENT_OPTIONS = frozenset({"mounts", "proxy", "transport"})
"""``httpx.AsyncClient`` options that would route requests around the
guarded transport.
"""

_FIXED_TRANSPORT_OPTIONS = frozenset(
    {"cert", "http1", "http2", "trust_env", "verify"}
)
"""``httpx.AsyncClient`` options that only configure the transport httpx
builds itself.

A client given a transport ignores them, so a caller passing one would
believe a TLS or protocol setting is in effect when the guarded transport's
own fixed settings are.
"""


class GuardedTransport(httpx.AsyncHTTPTransport):
    """An httpx transport whose sockets dial guard-validated addresses.

    HTTP/1.1 only: HTTP/2 stays off, so there is no connection coalescing
    across hostnames either.

    Parameters
    ----------
    limits
        Connection pool limits.
    network_backend
        The backend that actually opens sockets, wrapped by
        `PinningNetworkBackend`. Defaults to httpcore's own; injectable
        for testing.
    resolve_host
        The resolver for the backend's fail-closed fallback guard.
    logger
        Logger for the backend's fallback warning.
    """

    def __init__(
        self,
        *,
        limits: httpx.Limits = DEFAULT_LIMITS,
        network_backend: httpcore.AsyncNetworkBackend | None = None,
        resolve_host: HostResolver | None = None,
        logger: BoundLogger | None = None,
    ) -> None:
        # The parent constructor is deliberately not called: its only state
        # is this pool, which it would build around httpcore's default,
        # unpinned network backend. Verified against httpx 0.28.1, which
        # pyproject.toml bounds to <0.29; a later release that adds
        # transport state fails test_guarded_transport_builds_all_parent_state.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=True,
            http2=False,
            network_backend=PinningNetworkBackend(
                backend=network_backend,
                resolve_host=resolve_host,
                logger=logger,
            ),
        )


def create_guarded_client(
    *,
    limits: httpx.Limits | None = None,
    network_backend: httpcore.AsyncNetworkBackend | None = None,
    resolve_host: HostResolver | None = None,
    logger: BoundLogger | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Create an HTTP client for fetching user-supplied URLs.

    Every connection the client opens dials the address the caller pinned
    with `pinned_to` after validating the host with `resolve_and_validate`,
    or, failing closed, an address the transport validates itself.

    Parameters
    ----------
    limits
        Connection pool limits. Defaults to `DEFAULT_LIMITS`.
    network_backend
        The backend that actually opens sockets; injectable for testing.
    resolve_host
        The resolver for the transport's fail-closed fallback guard.
    logger
        Logger for the transport's fallback warning.
    **kwargs
        Other ``httpx.AsyncClient`` options that httpx applies at the client
        layer, such as ``timeout``, ``headers`` or ``follow_redirects``.
        Two groups of options are refused: those that would route requests
        around the guarded transport (``transport``, ``mounts``, ``proxy``),
        and those that only configure a transport httpx builds itself
        (``verify``, ``cert``, ``trust_env``, ``http1``, ``http2``), which
        the client would silently ignore because the guarded transport
        fixes its own TLS context and speaks HTTP/1.1 only.

    Returns
    -------
    httpx.AsyncClient
        A client whose transport is a `GuardedTransport`. The caller owns
        it and must close it.

    Raises
    ------
    TypeError
        Raised if ``kwargs`` includes an option that would bypass the
        guarded transport or that the guarded transport would ignore. The
        message names the option.
    """
    forbidden = sorted(_FORBIDDEN_CLIENT_OPTIONS & kwargs.keys())
    if forbidden:
        raise TypeError(
            f"create_guarded_client() does not accept {', '.join(forbidden)}:"
            " it would route requests around the guarded transport"
        )
    fixed = sorted(_FIXED_TRANSPORT_OPTIONS & kwargs.keys())
    if fixed:
        raise TypeError(
            f"create_guarded_client() does not accept {', '.join(fixed)}:"
            " the guarded transport fixes its own TLS context and speaks"
            " HTTP/1.1 only, so the client would ignore it"
        )
    transport = GuardedTransport(
        limits=limits or DEFAULT_LIMITS,
        network_backend=network_backend,
        resolve_host=resolve_host,
        logger=logger,
    )
    return httpx.AsyncClient(transport=transport, **kwargs)
