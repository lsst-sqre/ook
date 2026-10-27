"""Tests for the guarded HTTP client's socket-level address pinning."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Sequence

import httpcore
import httpx
import pytest
import respx
from structlog.testing import capture_logs

from ook.services.guardedhttp import (
    DEFAULT_LIMITS,
    GuardedTransport,
    HostResolver,
    PinningNetworkBackend,
    create_guarded_client,
    pinned_to,
)
from tests.support.guardedhttp import FakeNetworkBackend

PYTHON_DOCS_IP = "151.101.192.223"
"""A Fastly address serving both docs.python.org and pypi.org."""

PYTHON_DOCS_URL = "https://docs.python.org/3/library/types.html"


def fixed_resolver(*addresses: str) -> HostResolver:
    """Return a resolver answering every lookup with ``addresses``."""

    async def resolve_host(host: str) -> Sequence[str]:
        return list(addresses)

    return resolve_host


@pytest.mark.asyncio
async def test_pinned_address_dialed_while_hostname_kept() -> None:
    """The socket connects to the pinned address while the request URL,
    the ``Host`` header, and TLS SNI all keep the hostname.
    """
    backend = FakeNetworkBackend()
    async with create_guarded_client(network_backend=backend) as client:
        with pinned_to("docs.python.org", PYTHON_DOCS_IP):
            response = await client.get(PYTHON_DOCS_URL)

    assert response.status_code == 200
    assert response.request.url.host == "docs.python.org"
    assert backend.dialed == [(PYTHON_DOCS_IP, 443)]
    [connection] = backend.connections
    assert connection.server_hostname == "docs.python.org"
    [request] = connection.requests
    assert request.host_header == "docs.python.org"
    assert request.target == "/3/library/types.html"


@pytest.mark.asyncio
async def test_hostnames_sharing_an_address_get_distinct_connections() -> None:
    """Two hostnames pinned to one address each get their own connection.

    This is the pool-key regression the socket-level pinning exists for:
    Fastly serves docs.python.org and pypi.org from the same addresses
    with different certificates, so a connection opened for one hostname
    must never carry a request for the other.
    """
    backend = FakeNetworkBackend()
    async with create_guarded_client(network_backend=backend) as client:
        with pinned_to("pypi.org", PYTHON_DOCS_IP):
            first = await client.get("https://pypi.org/")
        with pinned_to("docs.python.org", PYTHON_DOCS_IP):
            second = await client.get(PYTHON_DOCS_URL)

    assert first.status_code == second.status_code == 200
    assert backend.dialed == [(PYTHON_DOCS_IP, 443), (PYTHON_DOCS_IP, 443)]
    assert [c.server_hostname for c in backend.connections] == [
        "pypi.org",
        "docs.python.org",
    ]
    assert [len(c.requests) for c in backend.connections] == [1, 1]


@pytest.mark.asyncio
async def test_same_hostname_reuses_its_connection() -> None:
    """Keep-alive still works: a second request to the same hostname
    rides the connection the first one opened.
    """
    backend = FakeNetworkBackend()
    async with create_guarded_client(network_backend=backend) as client:
        for _ in range(2):
            with pinned_to("docs.python.org", PYTHON_DOCS_IP):
                response = await client.get(PYTHON_DOCS_URL)
            assert response.status_code == 200

    [connection] = backend.connections
    assert len(connection.requests) == 2


@pytest.mark.asyncio
async def test_unpinned_public_host_validated_by_backend() -> None:
    """A request with no pinned address fails closed: the backend guards
    the host itself, dials the address it validated, and logs that a
    caller skipped its pre-flight guard.
    """
    backend = FakeNetworkBackend()
    with capture_logs() as captured:
        async with create_guarded_client(
            network_backend=backend,
            resolve_host=fixed_resolver(PYTHON_DOCS_IP),
        ) as client:
            response = await client.get(PYTHON_DOCS_URL)

    assert response.status_code == 200
    assert backend.dialed == [(PYTHON_DOCS_IP, 443)]
    assert backend.connections[0].server_hostname == "docs.python.org"
    warnings = [e for e in captured if e["log_level"] == "warning"]
    assert len(warnings) == 1
    assert warnings[0]["host"] == "docs.python.org"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "10.1.2.3",  # private
        "127.0.0.1",  # loopback
        "169.254.169.254",  # link-local (cloud metadata)
        "fd00::1",  # unique-local
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
    ],
)
async def test_unpinned_private_host_refused(address: str) -> None:
    """A request with no pinned address to a host resolving to a
    non-public address is refused before any socket is opened.
    """
    backend = FakeNetworkBackend()
    async with create_guarded_client(
        network_backend=backend, resolve_host=fixed_resolver(address)
    ) as client:
        with pytest.raises(httpx.ConnectError) as excinfo:
            await client.get("https://internal.example.com/")

    assert backend.connections == []
    assert "internal.example.com" in str(excinfo.value)
    assert "non-public" in str(excinfo.value)


@pytest.mark.asyncio
async def test_unpinned_private_ip_literal_refused() -> None:
    """An IP-literal URL with no pinned address is refused by the
    backend's own guard without a lookup.
    """

    async def refuse_resolution(host: str) -> Sequence[str]:
        pytest.fail(f"Unexpected DNS lookup for {host!r}")

    backend = FakeNetworkBackend()
    async with create_guarded_client(
        network_backend=backend, resolve_host=refuse_resolution
    ) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("http://169.254.169.254/latest/meta-data/")

    assert backend.connections == []


@pytest.mark.asyncio
async def test_unpinned_unresolvable_host_refused() -> None:
    """A request with no pinned address to a host that does not resolve
    is refused before any socket is opened.
    """

    async def resolve_host(host: str) -> Sequence[str]:
        raise socket.gaierror(8, "nodename nor servname provided")

    backend = FakeNetworkBackend()
    async with create_guarded_client(
        network_backend=backend, resolve_host=resolve_host
    ) as client:
        with pytest.raises(httpx.ConnectError) as excinfo:
            await client.get("https://no-such-host.example.com/")

    assert backend.connections == []
    assert "could not be resolved" in str(excinfo.value)


@pytest.mark.asyncio
async def test_unpinned_lookup_held_to_connect_timeout() -> None:
    """The fallback guard's lookup counts against the connect timeout, so
    a resolver that never answers cannot stall the request forever.
    """

    async def resolve_host(host: str) -> Sequence[str]:
        await asyncio.sleep(30)
        return [PYTHON_DOCS_IP]

    backend = FakeNetworkBackend()
    async with create_guarded_client(
        network_backend=backend,
        resolve_host=resolve_host,
        timeout=httpx.Timeout(5.0, connect=0.05),
    ) as client:
        with pytest.raises(httpx.ConnectTimeout):
            await client.get(PYTHON_DOCS_URL)

    assert backend.connections == []


@pytest.mark.asyncio
async def test_create_guarded_client_uses_guarded_transport() -> None:
    """The factory returns an ``httpx.AsyncClient`` whose transport is a
    `GuardedTransport` opening sockets through the pinning backend, with
    HTTP/2 off and a plain client's default pool limits.
    """
    async with create_guarded_client() as client:
        transport = client._transport
        assert isinstance(transport, GuardedTransport)
        pool = transport._pool
        assert type(pool) is httpcore.AsyncConnectionPool
        assert isinstance(pool._network_backend, PinningNetworkBackend)
        assert pool._http2 is False
        assert pool._max_connections == DEFAULT_LIMITS.max_connections
        assert (
            pool._max_keepalive_connections
            == DEFAULT_LIMITS.max_keepalive_connections
        )
        assert pool._keepalive_expiry == DEFAULT_LIMITS.keepalive_expiry == 5


def test_guarded_transport_builds_all_parent_state() -> None:
    """`GuardedTransport` skips ``httpx.AsyncHTTPTransport.__init__`` and
    builds the parent's only state, ``_pool``, itself.

    If an httpx upgrade gives the parent transport any other instance
    state, this fails at test time rather than the guarded client failing
    at request time; re-verify the constructor against the new release.
    """
    parent_state = set(vars(httpx.AsyncHTTPTransport()))
    assert parent_state == {"_pool"}
    assert set(vars(GuardedTransport())) == parent_state


@pytest.mark.asyncio
async def test_create_guarded_client_honors_limits() -> None:
    """Explicit limits configure the guarded transport's pool."""
    limits = httpx.Limits(
        max_connections=7, max_keepalive_connections=3, keepalive_expiry=2.5
    )
    async with create_guarded_client(limits=limits) as client:
        assert isinstance(client._transport, GuardedTransport)
        pool = client._transport._pool
        assert pool._max_connections == 7
        assert pool._max_keepalive_connections == 3
        assert pool._keepalive_expiry == 2.5


@pytest.mark.asyncio
async def test_create_guarded_client_passes_client_options() -> None:
    """Other ``httpx.AsyncClient`` options reach the client."""
    async with create_guarded_client(
        headers={"User-Agent": "ook-test"}, timeout=3.0
    ) as client:
        assert client.headers["User-Agent"] == "ook-test"
        assert client.timeout == httpx.Timeout(3.0)


@pytest.mark.parametrize("option", ["transport", "mounts", "proxy"])
def test_create_guarded_client_refuses_bypass_options(option: str) -> None:
    """Options that would route requests around the guarded transport are
    refused rather than silently opening an unguarded path.
    """
    with pytest.raises(TypeError, match=option):
        create_guarded_client(**{option: None})


@pytest.mark.asyncio
async def test_respx_intercepts_above_the_backend(
    respx_mock: respx.Router,
) -> None:
    """Respx patches the connection pool, above the network backend, so
    respx-based suites see guarded-client requests unchanged and no socket
    is opened.
    """
    route = respx_mock.get(PYTHON_DOCS_URL).respond(200)
    backend = FakeNetworkBackend()
    async with create_guarded_client(network_backend=backend) as client:
        with pinned_to("docs.python.org", PYTHON_DOCS_IP):
            response = await client.get(PYTHON_DOCS_URL)

    assert response.status_code == 200
    assert route.call_count == 1
    assert route.calls.last.request.url.host == "docs.python.org"
    assert backend.connections == []
