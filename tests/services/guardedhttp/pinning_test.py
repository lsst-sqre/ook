"""Tests for the context-variable handoff of pinned addresses."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from ook.services.guardedhttp import (
    NonPublicHostError,
    create_guarded_client,
    pinned_address,
    pinned_to,
)
from tests.support.guardedhttp import FakeNetworkBackend

PUBLIC_IP = "151.101.192.223"
"""A public address to pin hosts to."""

OTHER_PUBLIC_IP = "151.101.0.223"
"""A second public address to pin hosts to."""


async def refuse_resolution(host: str) -> Sequence[str]:
    """Fail the test if the backend falls back to its own lookup."""
    pytest.fail(f"Unexpected DNS lookup for {host!r}")


def test_pin_scoped_to_its_block() -> None:
    """A pin applies inside its block only."""
    assert pinned_address("docs.python.org") is None
    with pinned_to("docs.python.org", PUBLIC_IP):
        assert pinned_address("docs.python.org") == PUBLIC_IP
        assert pinned_address("pypi.org") is None
    assert pinned_address("docs.python.org") is None


def test_nested_pins_restore_outer_pins() -> None:
    """An inner block keeps the outer block's pins for other hosts, may
    re-pin the same host, and restores the outer pins on exit.
    """
    with pinned_to("docs.python.org", PUBLIC_IP):
        with (
            pinned_to("pypi.org", OTHER_PUBLIC_IP),
            pinned_to("docs.python.org", OTHER_PUBLIC_IP),
        ):
            assert pinned_address("pypi.org") == OTHER_PUBLIC_IP
            assert pinned_address("docs.python.org") == OTHER_PUBLIC_IP
        assert pinned_address("docs.python.org") == PUBLIC_IP
        assert pinned_address("pypi.org") is None


@pytest.mark.asyncio
async def test_pins_do_not_leak_across_tasks() -> None:
    """A pin set in one task is invisible to a task already running."""
    pinned = asyncio.Event()
    seen: list[str | None] = []

    async def observer() -> None:
        await pinned.wait()
        seen.append(pinned_address("docs.python.org"))

    task = asyncio.create_task(observer())
    with pinned_to("docs.python.org", PUBLIC_IP):
        pinned.set()
        await task
    assert seen == [None]


@pytest.mark.parametrize(
    ("pinned_as", "looked_up_as"),
    [
        ("DOCS.Python.org", "docs.python.org"),
        ("bücher.de", "xn--bcher-kva.de"),
        ("[2A04:4E42::223]", "2a04:4e42::223"),
        ("2a04:4e42::223", "2A04:4E42::223"),
    ],
)
def test_pin_matches_host_however_spelled(
    pinned_as: str, looked_up_as: str
) -> None:
    """A pin made with the caller's spelling of a host is found under the
    spelling httpcore hands the backend (IDNA-encoded, unbracketed), and
    vice versa.
    """
    with pinned_to(pinned_as, PUBLIC_IP):
        assert pinned_address(looked_up_as) == PUBLIC_IP


@pytest.mark.asyncio
async def test_unicode_pin_reaches_the_socket() -> None:
    """A host pinned by its Unicode name, as `urllib.parse.urlsplit`
    reports it, is dialed at the pinned address with no fallback lookup.
    """
    backend = FakeNetworkBackend()
    async with create_guarded_client(
        network_backend=backend, resolve_host=refuse_resolution
    ) as client:
        with pinned_to("bücher.de", PUBLIC_IP):
            response = await client.get("https://bücher.de/")

    assert response.status_code == 200
    assert backend.dialed == [(PUBLIC_IP, 443)]
    assert backend.connections[0].server_hostname == "xn--bcher-kva.de"


@pytest.mark.parametrize(
    "address", ["10.1.2.3", "127.0.0.1", "169.254.169.254", "fd00::1"]
)
def test_pinning_a_non_public_address_refused(address: str) -> None:
    """A caller that skipped the guard cannot pin a socket to a
    non-public address.
    """
    with (
        pytest.raises(NonPublicHostError),
        pinned_to("docs.python.org", address),
    ):
        pytest.fail("pinned_to accepted a non-public address")


def test_pinning_a_hostname_refused() -> None:
    """Only an IP literal can be pinned: pinning a hostname would have the
    socket re-resolve it and reopen the rebinding gap.
    """
    with (
        pytest.raises(ValueError, match="does not appear to be an IP"),
        pinned_to("docs.python.org", "dualstack.python.map.fastly.net"),
    ):
        pytest.fail("pinned_to accepted a hostname")
