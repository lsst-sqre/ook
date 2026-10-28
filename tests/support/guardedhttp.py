"""A fake httpcore network backend recording the sockets it opens.

`ook.services.guardedhttp` pins connections below httpx, at the socket
layer, so respx (which patches the connection pool, above the network
backend) cannot see where a request actually connects. Tests that need to
assert on the socket target build the guarded client with
`FakeNetworkBackend` instead: it records every ``connect_tcp`` target and
the TLS ``server_hostname`` of each connection, and answers every HTTP/1.1
request on it with a canned ``200`` -- or, given a responder, with whatever
response the responder builds for that connection and request.
"""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpcore

__all__ = [
    "FakeConnection",
    "FakeNetworkBackend",
    "FakeRequest",
    "FakeResponder",
    "raw_response",
]


@dataclass(slots=True)
class FakeRequest:
    """One HTTP request written to a fake connection."""

    method: str
    """The request method."""

    target: str
    """The request target (path and query)."""

    host_header: str | None
    """The ``Host`` header the request carried, if any."""


@dataclass(slots=True)
class FakeConnection:
    """One socket `FakeNetworkBackend` opened."""

    host: str
    """The host ``connect_tcp`` was asked to dial."""

    port: int
    """The port ``connect_tcp`` was asked to dial."""

    server_hostname: str | None = None
    """The hostname TLS was started with (SNI and certificate
    verification), or None if the connection never started TLS.
    """

    requests: list[FakeRequest] = field(default_factory=list)
    """Requests written to this connection, in order."""

    closed: bool = False
    """Whether the client closed the connection."""


FakeResponder = Callable[[FakeConnection, FakeRequest], bytes]
"""Type of a callable building the raw HTTP/1.1 response to a request
written to a fake connection (see `raw_response`).
"""


def raw_response(
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
    *,
    body: bytes = b"",
) -> bytes:
    """Build a raw HTTP/1.1 response for a fake connection.

    Parameters
    ----------
    status_code
        The response status code.
    headers
        Extra response headers. A ``Content-Length`` matching ``body`` is
        always sent.
    body
        The response body. Bodiless by default.

    Returns
    -------
    bytes
        The response head and body, ready to be read off a fake stream.
    """
    lines = [f"HTTP/1.1 {status_code} Fake", f"Content-Length: {len(body)}"]
    lines.extend(f"{name}: {value}" for name, value in (headers or {}).items())
    head = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
    return head + body


def _respond_ok(connection: FakeConnection, request: FakeRequest) -> bytes:
    """Answer every request with a bodiless ``200``."""
    return raw_response(200)


class _FakeStream(httpcore.AsyncNetworkStream):
    """A network stream answering each request through a responder."""

    def __init__(
        self, connection: FakeConnection, responder: FakeResponder
    ) -> None:
        self._connection = connection
        self._responder = responder
        self._written = b""
        self._pending: list[bytes] = []

    async def read(
        self, max_bytes: int, timeout: float | None = None
    ) -> bytes:
        return self._pending.pop(0) if self._pending else b""

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._written += buffer
        while b"\r\n\r\n" in self._written:
            head, self._written = self._written.split(b"\r\n\r\n", 1)
            request = _parse_request(head)
            self._connection.requests.append(request)
            self._pending.append(self._responder(self._connection, request))

    async def aclose(self) -> None:
        self._connection.closed = True

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self._connection.server_hostname = server_hostname
        return self

    def get_extra_info(self, info: str) -> Any:
        return None


class FakeNetworkBackend(httpcore.AsyncNetworkBackend):
    """A network backend that records sockets instead of opening them.

    Parameters
    ----------
    responder
        Builds the response to each request from the connection it was
        written to (whose ``server_hostname`` names the hostname the request
        was made for) and the request itself. Defaults to a bodiless
        ``200`` for every request.
    """

    def __init__(self, responder: FakeResponder = _respond_ok) -> None:
        self.connections: list[FakeConnection] = []
        self._responder = responder

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        connection = FakeConnection(host=host, port=port)
        self.connections.append(connection)
        return _FakeStream(connection, self._responder)

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise NotImplementedError

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    @property
    def dialed(self) -> list[tuple[str, int]]:
        """The ``(host, port)`` of every socket opened, in order."""
        return [(c.host, c.port) for c in self.connections]


def _parse_request(head: bytes) -> FakeRequest:
    """Parse an HTTP/1.1 request head into a `FakeRequest`."""
    request_line, *header_lines = head.decode("latin-1").split("\r\n")
    method, target, _ = request_line.split(" ", 2)
    host_header = None
    for line in header_lines:
        name, _, value = line.partition(":")
        if name.strip().lower() == "host":
            host_header = value.strip()
    return FakeRequest(method=method, target=target, host_header=host_header)
