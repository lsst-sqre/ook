"""Tests for the SSRF guard's resolve-and-validate step."""

from __future__ import annotations

import socket
from collections.abc import Sequence

import pytest

from ook.services.guardedhttp import (
    HostGuardError,
    HostResolutionError,
    HostResolver,
    NonPublicHostError,
    resolve_and_validate,
)

PRIVATE_ADDRESSES = [
    "127.0.0.1",  # loopback
    "10.0.0.5",  # private
    "192.168.1.10",  # private
    "169.254.169.254",  # link-local (cloud metadata)
    "0.0.0.0",  # noqa: S104 (unspecified, refused, never bound)
    "::1",  # IPv6 loopback
    "fe80::1",  # IPv6 link-local
    "fd00::1",  # IPv6 unique-local
]
"""Non-public addresses of every class the guard must refuse."""


def fixed_resolver(*addresses: str) -> HostResolver:
    """Return a resolver answering every lookup with ``addresses``."""

    async def resolve_host(host: str) -> Sequence[str]:
        return list(addresses)

    return resolve_host


def failing_resolver(error: Exception) -> HostResolver:
    """Return a resolver that raises ``error`` on every lookup."""

    async def resolve_host(host: str) -> Sequence[str]:
        raise error

    return resolve_host


async def refuse_resolution(host: str) -> Sequence[str]:
    """Fail the test if the guard performs a lookup."""
    pytest.fail(f"Unexpected DNS lookup for {host!r}")


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["151.101.192.223", "2a04:4e42::223"])
async def test_public_ip_literal_returned_without_lookup(host: str) -> None:
    """A public IP literal is validated directly and returned as is."""
    address = await resolve_and_validate(host, resolve_host=refuse_resolution)
    assert address == host


@pytest.mark.asyncio
@pytest.mark.parametrize("host", PRIVATE_ADDRESSES)
async def test_private_ip_literal_refused_without_lookup(host: str) -> None:
    """A non-public IP literal is refused without a lookup."""
    with pytest.raises(NonPublicHostError) as excinfo:
        await resolve_and_validate(host, resolve_host=refuse_resolution)
    assert excinfo.value.host == host
    assert excinfo.value.address == host


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host", ["::ffff:169.254.169.254", "::ffff:10.0.0.1", "::ffff:127.0.0.1"]
)
async def test_ipv4_mapped_private_literal_refused(host: str) -> None:
    """An IPv4-mapped IPv6 literal is judged by the IPv4 address it
    embeds, so wrapping a private address does not smuggle it through.
    """
    with pytest.raises(NonPublicHostError):
        await resolve_and_validate(host, resolve_host=refuse_resolution)


@pytest.mark.asyncio
async def test_ipv4_mapped_public_literal_accepted() -> None:
    """An IPv4-mapped IPv6 literal embedding a public address passes."""
    host = "::ffff:151.101.192.223"
    address = await resolve_and_validate(host, resolve_host=refuse_resolution)
    assert address == host


@pytest.mark.asyncio
async def test_public_hostname_returns_first_address() -> None:
    """A hostname resolving only to public addresses returns the first
    one, which is the address the caller pins its connection to.
    """
    address = await resolve_and_validate(
        "docs.python.org",
        resolve_host=fixed_resolver("151.101.192.223", "151.101.0.223"),
    )
    assert address == "151.101.192.223"


@pytest.mark.asyncio
async def test_resolver_receives_the_host() -> None:
    """The resolver is asked about exactly the host being validated."""
    lookups: list[str] = []

    async def resolve_host(host: str) -> Sequence[str]:
        lookups.append(host)
        return ["151.101.192.223"]

    await resolve_and_validate("docs.python.org", resolve_host=resolve_host)
    assert lookups == ["docs.python.org"]


@pytest.mark.asyncio
@pytest.mark.parametrize("address", PRIVATE_ADDRESSES)
async def test_private_hostname_refused(address: str) -> None:
    """A hostname resolving to a non-public address is refused."""
    with pytest.raises(NonPublicHostError) as excinfo:
        await resolve_and_validate(
            "internal.example.com", resolve_host=fixed_resolver(address)
        )
    assert excinfo.value.host == "internal.example.com"
    assert excinfo.value.address == address
    assert "internal.example.com" in str(excinfo.value)


@pytest.mark.asyncio
async def test_hostname_with_any_private_address_refused() -> None:
    """Every resolved address must be public, not just the first: the
    connection could otherwise be made to any of them.
    """
    with pytest.raises(NonPublicHostError) as excinfo:
        await resolve_and_validate(
            "mixed.example.com",
            resolve_host=fixed_resolver("151.101.192.223", "10.1.2.3"),
        )
    assert excinfo.value.address == "10.1.2.3"


@pytest.mark.asyncio
async def test_hostname_resolving_to_ipv4_mapped_private_refused() -> None:
    """A resolver answer that IPv4-maps a private address is refused."""
    with pytest.raises(NonPublicHostError):
        await resolve_and_validate(
            "mapped.example.com",
            resolve_host=fixed_resolver("::ffff:127.0.0.1"),
        )


@pytest.mark.asyncio
async def test_unresolvable_host_is_resolution_error() -> None:
    """A resolver failure is a resolution error carrying the cause."""
    error = socket.gaierror(8, "nodename nor servname provided")
    with pytest.raises(HostResolutionError) as excinfo:
        await resolve_and_validate(
            "no-such-host.example.com", resolve_host=failing_resolver(error)
        )
    assert excinfo.value.host == "no-such-host.example.com"
    assert excinfo.value.__cause__ is error
    assert "no-such-host.example.com" in str(excinfo.value)


@pytest.mark.asyncio
async def test_idna_failure_is_resolution_error() -> None:
    """A host label IDNA cannot encode is a resolution error, not an
    escaping ``UnicodeError``.
    """
    host = f"{'a' * 70}.example.com"
    error = UnicodeEncodeError("idna", host, 0, len(host), "label too long")
    with pytest.raises(HostResolutionError) as excinfo:
        await resolve_and_validate(host, resolve_host=failing_resolver(error))
    assert excinfo.value.__cause__ is error


@pytest.mark.asyncio
async def test_empty_resolution_is_resolution_error() -> None:
    """A resolver answering with no addresses is a resolution error."""
    with pytest.raises(HostResolutionError):
        await resolve_and_validate(
            "empty.example.com", resolve_host=fixed_resolver()
        )


def test_refusals_are_distinct_types() -> None:
    """The two refusals share a base but are not each other, so callers
    can classify a non-public host apart from a resolution failure.
    """
    assert issubclass(NonPublicHostError, HostGuardError)
    assert issubclass(HostResolutionError, HostGuardError)
    assert not issubclass(NonPublicHostError, HostResolutionError)
    assert not issubclass(HostResolutionError, NonPublicHostError)
