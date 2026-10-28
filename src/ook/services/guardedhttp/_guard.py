"""Resolve a host and validate that every address it names is public."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence

__all__ = [
    "HostGuardError",
    "HostResolutionError",
    "HostResolver",
    "NonPublicHostError",
    "default_resolve_host",
    "resolve_and_validate",
]

HostResolver = Callable[[str], Awaitable[Sequence[str]]]
"""Type of a callable resolving a hostname to IP address strings."""


class HostGuardError(Exception):
    """The SSRF guard refused a host.

    The base of the guard's two refusals, which callers usually need to
    tell apart: `NonPublicHostError` is a fact about the URL, while
    `HostResolutionError` is an upstream condition that may clear by
    itself.

    Parameters
    ----------
    message
        The description of the refusal.
    host
        The refused host.
    """

    def __init__(self, message: str, *, host: str) -> None:
        super().__init__(message)
        self.host = host


class NonPublicHostError(HostGuardError):
    """A host is, or resolves to, an address that is not globally
    routable (private, loopback, link-local, unique-local, reserved).

    This is a fact about the URL the caller chose, so it is never worth
    retrying: the link checker reports such a URL as ``unsupported`` and
    the intersphinx cache answers it with a 400.

    Parameters
    ----------
    host
        The refused host.
    address
        The first non-public address the host is or resolved to.
    """

    def __init__(self, *, host: str, address: str) -> None:
        super().__init__(
            f"Host {host!r} resolves to the non-public address {address}",
            host=host,
        )
        self.address = address


class HostResolutionError(HostGuardError):
    """A hostname could not be resolved to any address.

    This is the absence of an answer rather than a fact about the URL — a
    name that does not resolve today may resolve tomorrow — so the link
    checker keeps such a URL on its retry ladder and the intersphinx cache
    answers it with a 502. The resolver's own exception, if any, is the
    ``__cause__``.

    Parameters
    ----------
    message
        The description of the failure.
    host
        The hostname that did not resolve.
    """


async def resolve_and_validate(
    host: str, *, resolve_host: HostResolver | None = None
) -> str:
    """Resolve a host and validate that it names only public addresses.

    This is Ook's single SSRF guard for outbound requests to
    user-supplied URLs. An IP literal is validated directly, without a
    lookup. A hostname is resolved, and every address it resolves to must
    be globally routable, because the connection could otherwise be made
    to any of them; an IPv4-mapped IPv6 address is judged by the IPv4
    address it embeds.

    Parameters
    ----------
    host
        The bare host to validate: a hostname, or an unbracketed IP
        literal, as `urllib.parse.urlsplit` or ``httpx.URL.host`` reports
        it.
    resolve_host
        The hostname resolver, mainly injectable for testing. Defaults to
        `default_resolve_host`.

    Returns
    -------
    str
        The validated address to connect to: the host itself when it is an
        IP literal, otherwise the first address it resolved to. Connecting
        to exactly this address (see `pinned_to`) is what closes the
        DNS-rebinding gap between this check and the connect.

    Raises
    ------
    NonPublicHostError
        Raised if the host is, or resolves to, any non-public address.
    HostResolutionError
        Raised if the resolver fails (``OSError``, or ``UnicodeError`` for
        a label IDNA cannot encode) or answers with no addresses.
    """
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        check_public(host, literal)
        return host

    resolver = resolve_host or default_resolve_host
    try:
        resolved = list(await resolver(host))
    except (OSError, UnicodeError) as exc:
        # getaddrinfo reports an unknown host or a transient DNS failure
        # as socket.gaierror (an OSError), and a host label IDNA cannot
        # encode -- an empty label, or an all-ASCII one over 63
        # characters, both of which survive URL parsing -- as
        # UnicodeEncodeError. Neither is an httpx.HTTPError, so both are
        # funnelled into the one resolution-failure type callers handle.
        raise HostResolutionError(
            f"Host {host!r} could not be resolved: {_describe(exc)}",
            host=host,
        ) from exc
    if not resolved:
        raise HostResolutionError(
            f"Host {host!r} did not resolve to any address", host=host
        )
    for address in resolved:
        check_public(host, ipaddress.ip_address(address))
    return resolved[0]


def check_public(
    host: str, address: ipaddress.IPv4Address | ipaddress.IPv6Address
) -> None:
    """Refuse an address that is not globally routable.

    Parameters
    ----------
    host
        The host the address belongs to, named in the refusal.
    address
        The address to check. For an IPv4-mapped IPv6 address, the
        embedded IPv4 address is what is judged.

    Raises
    ------
    NonPublicHostError
        Raised if the address is not globally routable.
    """
    candidate = (
        address.ipv4_mapped
        if isinstance(address, ipaddress.IPv6Address)
        and address.ipv4_mapped is not None
        else address
    )
    if not candidate.is_global:
        raise NonPublicHostError(host=host, address=str(address))


async def default_resolve_host(host: str) -> Sequence[str]:
    """Resolve a hostname to IP address strings with getaddrinfo.

    Parameters
    ----------
    host
        The hostname to resolve.

    Returns
    -------
    list of str
        Every address the hostname resolved to, in resolver order.
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _describe(error: Exception) -> str:
    """Format an exception as a short diagnostic string."""
    message = str(error)
    if message:
        return f"{type(error).__name__}: {message}"
    return type(error).__name__
