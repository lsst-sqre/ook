"""Outbound HTTP for user-supplied URLs, guarded against SSRF.

This package is Ook's single resolve-validate-pin implementation for every
outbound fetch of a URL Ook did not choose itself. It has three layers:

1. `resolve_and_validate` resolves a host and refuses it unless every
   address it names is globally routable, returning the address to
   connect to.
2. `pinned_to` hands that address to the transport for the one request
   about to be made, through a context variable keyed by hostname.
3. The client from `create_guarded_client` opens its sockets through
   `PinningNetworkBackend`, which dials the pinned address rather than
   re-resolving the hostname (closing the DNS-rebinding gap), and which
   fails closed -- running the guard itself -- for a request made with no
   pinned address.

**No service rewrites a URL host to an IP.** The request URL keeps its
hostname, so TLS SNI and certificate verification use the hostname, and
httpcore's connection pool, keyed on ``(scheme, host, port)``, reuses a
connection only for the same hostname. Pinning by rewriting the URL host
instead keys the pool by IP, so on a CDN serving several hostnames from one
address a request for one hostname rides a connection opened for another,
and the CDN answers it with a 421 Misdirected Request.
"""

from ._guard import (
    HostGuardError,
    HostResolutionError,
    HostResolver,
    NonPublicHostError,
    default_resolve_host,
    resolve_and_validate,
)
from ._pinning import (
    GuardRefusedConnectError,
    PinningNetworkBackend,
    pinned_address,
    pinned_to,
)
from ._transport import DEFAULT_LIMITS, GuardedTransport, create_guarded_client

__all__ = [
    "DEFAULT_LIMITS",
    "GuardRefusedConnectError",
    "GuardedTransport",
    "HostGuardError",
    "HostResolutionError",
    "HostResolver",
    "NonPublicHostError",
    "PinningNetworkBackend",
    "create_guarded_client",
    "default_resolve_host",
    "pinned_address",
    "pinned_to",
    "resolve_and_validate",
]
