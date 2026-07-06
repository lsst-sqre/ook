"""Tests for the LinkCheckService's check execution."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import structlog

from ook.config import config
from ook.domain.linkcheck import (
    CheckRunStatus,
    CheckUrlStatus,
    LinkState,
    LinkStatus,
    RetryLadderConfig,
    SubmittedUrl,
)
from ook.factory import Factory
from ook.services.linkcheck import LinkCheckService, UrlChecker

PUBLIC_IP = "93.184.216.34"
"""A public (globally-routable) IPv4 address for fake DNS resolution."""


async def _resolve_public(host: str) -> Sequence[str]:
    """Resolve every hostname to a public IP without real DNS."""
    return [PUBLIC_IP]


def make_service(
    factory: Factory, http_client: httpx.AsyncClient
) -> LinkCheckService:
    """Create a LinkCheckService whose UrlChecker uses the given client
    and a fake DNS resolver, so tests never touch the network.
    """
    logger = structlog.get_logger("test")
    checker = UrlChecker(
        http_client=http_client,
        logger=logger,
        request_timeout=timedelta(seconds=5),
        max_concurrency=10,
        host_interval=timedelta(seconds=0),
        resolve_host=_resolve_public,
    )
    return LinkCheckService(
        linkcheck_store=factory.create_linkcheck_store(),
        logger=logger,
        freshness_ttl=config.linkcheck_freshness_ttl,
        max_urls_per_check=config.linkcheck_max_urls_per_check,
        url_checker=checker,
        retry_ladder=RetryLadderConfig(),
    )


def mock_transport(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.MockTransport:
    """Wrap a request handler as an httpx transport."""
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_execute_check_completes_with_ok_result(
    factory: Factory,
) -> None:
    """Executing a submitted check updates the due URL's status via the
    transition engine and marks the check complete, with the results
    visible in the check report.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=mock_transport(handler)) as hc:
        service = make_service(factory, hc)
        async with factory.db_session.begin():
            submission = await service.submit_check(
                ltd_slug="sqr-000",
                default_branch=True,
                urls=[
                    SubmittedUrl(url="https://example.com/page", paths=["a"])
                ],
            )
            await service.execute_check(submission.check_id)

            report = await service.get_check_report(submission.check_id)
            assert report is not None
            assert report.status is CheckRunStatus.complete
            assert report.date_completed is not None
            (url_report,) = report.urls
            assert url_report.url == "https://example.com/page"
            assert url_report.status is CheckUrlStatus.ok
            assert url_report.status_code == 200
            assert url_report.checked_at is not None


@pytest.mark.asyncio
async def test_execute_check_never_ok_failure_is_broken(
    factory: Factory,
) -> None:
    """A failing URL that has never been seen OK is broken immediately,
    with the failure bookkeeping persisted.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=mock_transport(handler)) as hc:
        service = make_service(factory, hc)
        store = factory.create_linkcheck_store()
        async with factory.db_session.begin():
            submission = await service.submit_check(
                ltd_slug="sqr-000",
                default_branch=True,
                urls=[
                    SubmittedUrl(url="https://example.com/gone", paths=["a"])
                ],
            )
            await service.execute_check(submission.check_id)

            state = await store.get_url_state("https://example.com/gone")
            assert state is not None
            assert state.status is LinkStatus.broken
            assert state.status_code == 404
            assert state.failing_since == state.checked_at
            assert state.failure_count == 1
            assert state.next_check_at is None
            assert state.error is not None
            assert "404" in state.error

            report = await service.get_check_report(submission.check_id)
            assert report is not None
            assert report.status is CheckRunStatus.complete
            (url_report,) = report.urls
            assert url_report.status is CheckUrlStatus.broken


@pytest.mark.asyncio
async def test_execute_check_previously_ok_failure_bookkeeping(
    factory: Factory,
) -> None:
    """A previously-OK URL that starts failing reports failing with the
    retry-ladder bookkeeping persisted: first-failed timestamp,
    consecutive-failure count, and next-check-due.
    """
    last_ok = datetime.now(tz=UTC).replace(microsecond=0) - timedelta(hours=25)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=mock_transport(handler)) as hc:
        service = make_service(factory, hc)
        store = factory.create_linkcheck_store()
        async with factory.db_session.begin():
            await store.upsert_url_state(
                LinkState(
                    url="https://example.com/flaky",
                    status=LinkStatus.ok,
                    checked_at=last_ok,
                    last_ok_at=last_ok,
                    status_code=200,
                )
            )
            submission = await service.submit_check(
                ltd_slug="sqr-000",
                default_branch=True,
                urls=[
                    SubmittedUrl(url="https://example.com/flaky", paths=["a"])
                ],
            )
            await service.execute_check(submission.check_id)

            state = await store.get_url_state("https://example.com/flaky")
            assert state is not None
            assert state.status is LinkStatus.failing
            assert state.status_code == 503
            assert state.last_ok_at == last_ok
            assert state.failing_since == state.checked_at
            assert state.failure_count == 1
            # The first rung of the retry ladder schedules the recheck.
            assert state.next_check_at == state.checked_at + timedelta(hours=1)

            report = await service.get_check_report(submission.check_id)
            assert report is not None
            (url_report,) = report.urls
            assert url_report.status is CheckUrlStatus.failing


@pytest.mark.asyncio
async def test_execute_check_persists_redirect_metadata(
    factory: Factory,
) -> None:
    """A URL resolving via a permanent redirect is reported redirected
    with the redirect metadata persisted.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(
                301, headers={"Location": "https://example.com/new"}
            )
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=mock_transport(handler)) as hc:
        service = make_service(factory, hc)
        async with factory.db_session.begin():
            submission = await service.submit_check(
                ltd_slug="sqr-000",
                default_branch=True,
                urls=[
                    SubmittedUrl(url="https://example.com/old", paths=["a"])
                ],
            )
            await service.execute_check(submission.check_id)

            report = await service.get_check_report(submission.check_id)
            assert report is not None
            (url_report,) = report.urls
            assert url_report.status is CheckUrlStatus.redirected
            assert url_report.status_code == 200
            assert url_report.redirect_status_code == 301
            assert url_report.redirect_url == "https://example.com/new"


@pytest.mark.asyncio
async def test_execute_check_skips_fresh_urls(factory: Factory) -> None:
    """Member URLs with a fresh cached result are not refetched at
    execution; only due URLs are checked.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    fetched_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched_paths.append(request.url.path)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=mock_transport(handler)) as hc:
        service = make_service(factory, hc)
        store = factory.create_linkcheck_store()
        async with factory.db_session.begin():
            await store.upsert_url_state(
                LinkState(
                    url="https://example.com/fresh",
                    status=LinkStatus.ok,
                    checked_at=now,
                    last_ok_at=now,
                    status_code=200,
                )
            )
            submission = await service.submit_check(
                ltd_slug="sqr-000",
                default_branch=True,
                urls=[
                    SubmittedUrl(url="https://example.com/fresh", paths=["a"]),
                    SubmittedUrl(url="https://example.com/due", paths=["a"]),
                ],
            )
            await service.execute_check(submission.check_id)

            assert fetched_paths == ["/due"]
            report = await service.get_check_report(submission.check_id)
            assert report is not None
            assert report.status is CheckRunStatus.complete
            statuses = {u.url: u.status for u in report.urls}
            assert statuses == {
                "https://example.com/fresh": CheckUrlStatus.ok,
                "https://example.com/due": CheckUrlStatus.ok,
            }


@pytest.mark.asyncio
async def test_execute_unknown_check_is_noop(factory: Factory) -> None:
    """Executing an unknown check id neither raises nor fetches, so
    at-least-once delivery of stale execution requests is safe.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("No HTTP request expected")

    async with httpx.AsyncClient(transport=mock_transport(handler)) as hc:
        service = make_service(factory, hc)
        async with factory.db_session.begin():
            await service.execute_check(123456789)


def test_retry_ladder_configuration_defaults() -> None:
    """The retry-ladder thresholds are exposed as configuration
    settings with the PRD's defaults.
    """
    assert config.linkcheck_broken_threshold == timedelta(hours=48)
    assert config.linkcheck_broken_min_attempts == 3
    assert config.linkcheck_recheck_intervals == (
        timedelta(hours=1),
        timedelta(hours=4),
        timedelta(hours=24),
        timedelta(hours=48),
    )
