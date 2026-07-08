"""Tests for the UrlChecker service."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Sequence
from datetime import timedelta

import httpx
import pytest
import respx
import structlog

from ook.config import config
from ook.domain.linkcheck import CheckResult
from ook.factory import Factory
from ook.services.linkcheck import UrlChecker

PUBLIC_IP = "93.184.216.34"
"""A public (globally-routable) IPv4 address for fake DNS resolution."""


def make_checker(
    http_client: httpx.AsyncClient,
    *,
    max_concurrency: int = 10,
    host_interval: float = 0.0,
    ip_map: dict[str, Sequence[str]] | None = None,
) -> UrlChecker:
    """Create a UrlChecker with a fake DNS resolver.

    The fake resolver returns ``ip_map[host]`` when the host is mapped,
    and a public IP address otherwise, so tests never perform real DNS
    lookups.
    """

    async def resolve_host(host: str) -> Sequence[str]:
        if ip_map is not None and host in ip_map:
            return ip_map[host]
        return [PUBLIC_IP]

    return UrlChecker(
        http_client=http_client,
        logger=structlog.get_logger("test"),
        request_timeout=timedelta(seconds=5),
        max_concurrency=max_concurrency,
        host_interval=timedelta(seconds=host_interval),
        resolve_host=resolve_host,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "mailto:someone@example.com",
        "ftp://example.com/file.txt",
        "javascript:void(0)",
        "http://[invalid",
        "not a url",
        "",
    ],
)
async def test_unsupported_inputs_never_fetched(
    url: str,
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """Non-http(s) schemes and malformed URLs are classified as
    unsupported without any network fetch.
    """
    checker = make_checker(http_client)
    outcome = await checker.check(url)
    assert outcome.result is CheckResult.unsupported
    assert outcome.status_code is None
    assert outcome.error is not None
    assert len(respx_mock.calls) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/status",
        "http://10.0.0.5/",
        "http://192.168.1.10/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[fd00::1]/",
        "http://0.0.0.0/",
    ],
)
async def test_private_ip_literals_blocked(
    url: str,
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """Literal private, loopback, and link-local IP hosts are never
    fetched and come back unsupported.
    """
    checker = make_checker(http_client)
    outcome = await checker.check(url)
    assert outcome.result is CheckResult.unsupported
    assert outcome.error is not None
    assert len(respx_mock.calls) == 0


@pytest.mark.asyncio
async def test_private_hostname_blocked(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A hostname resolving to a private address is never fetched."""
    checker = make_checker(
        http_client, ip_map={"internal.example.com": ["10.1.2.3"]}
    )
    outcome = await checker.check("http://internal.example.com/page")
    assert outcome.result is CheckResult.unsupported
    assert outcome.error is not None
    assert len(respx_mock.calls) == 0


@pytest.mark.asyncio
async def test_dns_failure_is_failure(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A hostname that fails to resolve is a check failure (broken
    link), not an unsupported URL.
    """

    async def resolve_host(host: str) -> Sequence[str]:
        raise socket.gaierror(8, "nodename nor servname provided")

    checker = UrlChecker(
        http_client=http_client,
        logger=structlog.get_logger("test"),
        request_timeout=timedelta(seconds=5),
        resolve_host=resolve_host,
    )
    outcome = await checker.check("https://no-such-host.example.com/")
    assert outcome.result is CheckResult.failure
    assert outcome.status_code is None
    assert outcome.error is not None
    assert len(respx_mock.calls) == 0


@pytest.mark.asyncio
async def test_head_success(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A URL whose HEAD request succeeds resolves in one request."""
    respx_mock.route(
        method="HEAD", path="/page", headers={"Host": "example.com"}
    ).respond(200)

    checker = make_checker(http_client)
    outcome = await checker.check("https://example.com/page")
    assert outcome.result is CheckResult.success
    assert outcome.status_code == 200
    assert outcome.redirect_status_code is None
    assert outcome.redirect_url is None
    assert outcome.error is None
    assert len(respx_mock.calls) == 1


@pytest.mark.asyncio
async def test_request_pinned_to_validated_address(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """The outbound request connects to the exact address the SSRF guard
    validated, while the Host header preserves the original hostname.

    This is the regression test for the DNS-rebinding TOCTOU fix: the
    socket target must be the guard-validated IP so httpx cannot
    re-resolve the hostname to an internal address at connect time.
    """
    pinned_ip = "93.184.216.34"
    respx_mock.route(
        method="HEAD", path="/page", headers={"Host": "example.com"}
    ).respond(200)

    checker = make_checker(http_client, ip_map={"example.com": [pinned_ip]})
    outcome = await checker.check("https://example.com/page")
    assert outcome.result is CheckResult.success
    assert len(respx_mock.calls) == 1
    request = respx_mock.calls.last.request
    # The socket target is the validated IP...
    assert request.url.host == pinned_ip
    # ...while the Host header (and TLS SNI) stay the hostname.
    assert request.headers["Host"] == "example.com"


@pytest.mark.asyncio
async def test_get_fallback_success(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A URL whose HEAD request errors falls back to GET."""
    respx_mock.route(
        method="HEAD", path="/no-head", headers={"Host": "example.com"}
    ).respond(405)
    respx_mock.route(
        method="GET", path="/no-head", headers={"Host": "example.com"}
    ).respond(200)

    checker = make_checker(http_client)
    outcome = await checker.check("https://example.com/no-head")
    assert outcome.result is CheckResult.success
    assert outcome.status_code == 200
    assert outcome.error is None
    assert len(respx_mock.calls) == 2


@pytest.mark.asyncio
async def test_get_fallback_failure(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A URL failing both HEAD and GET is a failure with the GET's
    status code.
    """
    respx_mock.route(
        method="HEAD", path="/missing", headers={"Host": "example.com"}
    ).respond(405)
    respx_mock.route(
        method="GET", path="/missing", headers={"Host": "example.com"}
    ).respond(404)

    checker = make_checker(http_client)
    outcome = await checker.check("https://example.com/missing")
    assert outcome.result is CheckResult.failure
    assert outcome.status_code == 404
    assert outcome.error is not None
    assert "404" in outcome.error


@pytest.mark.asyncio
async def test_network_error_falls_back_to_get(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A transport error on HEAD (e.g. a server that drops HEAD
    requests) falls back to GET.
    """
    respx_mock.route(
        method="HEAD", path="/drops-head", headers={"Host": "example.com"}
    ).mock(side_effect=httpx.RemoteProtocolError)
    respx_mock.route(
        method="GET", path="/drops-head", headers={"Host": "example.com"}
    ).respond(200)

    checker = make_checker(http_client)
    outcome = await checker.check("https://example.com/drops-head")
    assert outcome.result is CheckResult.success
    assert outcome.status_code == 200


@pytest.mark.asyncio
async def test_timeout_is_failure_without_get_fallback(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A timeout on the HEAD request is an immediate failure; the GET
    fallback is not attempted (it would double the worst-case wait).
    """
    respx_mock.route(
        method="HEAD", path="/slow", headers={"Host": "example.com"}
    ).mock(side_effect=httpx.ConnectTimeout)

    checker = make_checker(http_client)
    outcome = await checker.check("https://example.com/slow")
    assert outcome.result is CheckResult.failure
    assert outcome.status_code is None
    assert outcome.error is not None
    assert "timed out" in outcome.error.lower()
    assert len(respx_mock.calls) == 1


@pytest.mark.asyncio
async def test_permanent_redirect_captured(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A permanent redirect resolves with the final location and the
    permanent redirect status code.
    """
    respx_mock.route(
        method="HEAD", path="/old", headers={"Host": "example.com"}
    ).respond(301, headers={"Location": "https://example.com/new"})
    respx_mock.route(
        method="HEAD", path="/new", headers={"Host": "example.com"}
    ).respond(200)

    checker = make_checker(http_client)
    outcome = await checker.check("https://example.com/old")
    assert outcome.result is CheckResult.success
    assert outcome.status_code == 200
    assert outcome.redirect_status_code == 301
    assert outcome.redirect_url == "https://example.com/new"


@pytest.mark.asyncio
async def test_temporary_redirect_captured(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A temporary redirect resolves with the final location and the
    temporary redirect status code.
    """
    respx_mock.route(
        method="HEAD", path="/moved", headers={"Host": "example.com"}
    ).respond(302, headers={"Location": "https://example.com/elsewhere"})
    respx_mock.route(
        method="HEAD", path="/elsewhere", headers={"Host": "example.com"}
    ).respond(200)

    checker = make_checker(http_client)
    outcome = await checker.check("https://example.com/moved")
    assert outcome.result is CheckResult.success
    assert outcome.status_code == 200
    assert outcome.redirect_status_code == 302
    assert outcome.redirect_url == "https://example.com/elsewhere"


@pytest.mark.asyncio
async def test_mixed_redirect_chain_is_temporary(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A chain mixing permanent and temporary redirects is reported as
    temporary: the source cannot safely be updated past a temporary hop.
    """
    respx_mock.route(
        method="HEAD", path="/a", headers={"Host": "example.com"}
    ).respond(301, headers={"Location": "https://example.com/b"})
    respx_mock.route(
        method="HEAD", path="/b", headers={"Host": "example.com"}
    ).respond(307, headers={"Location": "https://example.com/c"})
    respx_mock.route(
        method="HEAD", path="/c", headers={"Host": "example.com"}
    ).respond(200)

    checker = make_checker(http_client)
    outcome = await checker.check("https://example.com/a")
    assert outcome.result is CheckResult.success
    assert outcome.redirect_status_code == 307
    assert outcome.redirect_url == "https://example.com/c"


@pytest.mark.asyncio
async def test_relative_redirect_location(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A relative Location header is resolved against the request URL."""
    respx_mock.route(
        method="HEAD", path="/dir/old", headers={"Host": "example.com"}
    ).respond(308, headers={"Location": "/dir/new"})
    respx_mock.route(
        method="HEAD", path="/dir/new", headers={"Host": "example.com"}
    ).respond(200)

    checker = make_checker(http_client)
    outcome = await checker.check("https://example.com/dir/old")
    assert outcome.result is CheckResult.success
    assert outcome.redirect_status_code == 308
    assert outcome.redirect_url == "https://example.com/dir/new"


@pytest.mark.asyncio
async def test_redirect_to_private_host_blocked(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A redirect target resolving to a private address is never
    fetched; the SSRF guard applies to every hop.
    """
    respx_mock.route(
        method="HEAD", path="/sneaky", headers={"Host": "example.com"}
    ).respond(302, headers={"Location": "http://internal.example.com/secrets"})

    checker = make_checker(
        http_client, ip_map={"internal.example.com": ["192.168.0.10"]}
    )
    outcome = await checker.check("https://example.com/sneaky")
    assert outcome.result is CheckResult.unsupported
    assert outcome.error is not None
    # Only the public first hop was fetched.
    assert len(respx_mock.calls) == 1


@pytest.mark.asyncio
async def test_too_many_redirects_is_failure(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A redirect loop is reported as a failure."""
    respx_mock.route(
        method="HEAD", path="/loop", headers={"Host": "example.com"}
    ).respond(302, headers={"Location": "https://example.com/loop"})

    checker = make_checker(http_client)
    outcome = await checker.check("https://example.com/loop")
    assert outcome.result is CheckResult.failure
    assert outcome.error is not None
    assert "redirect" in outcome.error.lower()


@pytest.mark.asyncio
async def test_global_concurrency_cap(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """No more than max_concurrency requests are in flight at once,
    even across different hosts.
    """
    in_flight = 0
    peak = 0

    async def responder(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return httpx.Response(200)

    respx_mock.route(method="HEAD").mock(side_effect=responder)

    checker = make_checker(http_client, max_concurrency=2)
    urls = [f"https://host{n}.example.com/page" for n in range(8)]
    outcomes = await asyncio.gather(*(checker.check(url) for url in urls))
    assert all(o.result is CheckResult.success for o in outcomes)
    assert peak == 2


@pytest.mark.asyncio
async def test_per_host_politeness_interval(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """Concurrent checks of the same host are spaced by the politeness
    interval.
    """
    request_times: list[float] = []

    def responder(request: httpx.Request) -> httpx.Response:
        loop = asyncio.get_running_loop()
        request_times.append(loop.time())
        return httpx.Response(200)

    respx_mock.route(method="HEAD").mock(side_effect=responder)

    checker = make_checker(http_client, host_interval=0.2)
    await asyncio.gather(
        checker.check("https://example.com/one"),
        checker.check("https://example.com/two"),
    )
    assert len(request_times) == 2
    spacing = abs(request_times[1] - request_times[0])
    assert spacing >= 0.18


@pytest.mark.asyncio
async def test_politeness_interval_not_applied_across_hosts(
    http_client: httpx.AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """Checks of different hosts are not spaced by the politeness
    interval.
    """
    request_times: list[float] = []

    def responder(request: httpx.Request) -> httpx.Response:
        loop = asyncio.get_running_loop()
        request_times.append(loop.time())
        return httpx.Response(200)

    respx_mock.route(method="HEAD").mock(side_effect=responder)

    checker = make_checker(http_client, host_interval=1.0)
    await asyncio.gather(
        checker.check("https://alpha.example.com/"),
        checker.check("https://beta.example.com/"),
    )
    assert len(request_times) == 2
    spacing = abs(request_times[1] - request_times[0])
    assert spacing < 0.5


def test_linkcheck_configuration_defaults() -> None:
    """The link-check tuning knobs are exposed as configuration
    settings with defaults.
    """
    assert config.linkcheck_request_timeout == timedelta(seconds=30)
    assert config.linkcheck_max_concurrency == 10
    assert config.linkcheck_host_interval == timedelta(seconds=1)


@pytest.mark.asyncio
async def test_factory_provides_url_checker(factory: Factory) -> None:
    """The factory exposes a process-wide UrlChecker bound to the
    shared HTTP client.
    """
    checker = factory.url_checker
    assert isinstance(checker, UrlChecker)
    # The checker is a process-context singleton so its concurrency cap
    # is shared by all consumers.
    assert factory.url_checker is checker
    assert checker._http_client is factory.http_client
