"""Integration tests for the async link-check execution path via Kafka.

These tests exercise the full flow against the testcontainers Postgres
and Kafka: ``POST /ook/linkcheck/checks`` enqueues a Kafka message, the
FastStream subscriber executes the check, and polling the check shows it
complete with per-URL results.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import structlog
from httpx import AsyncClient
from safir.database import create_async_session, create_database_engine

from ook.config import config
from ook.domain.kafka import RecheckUrlsMessageV1
from ook.domain.linkcheck import LinkState, LinkStatus, UrlOccurrence
from ook.kafkarouter import kafka_router
from ook.storage.linkcheckstore import LinkCheckStore
from tests.support.github import GitHubMocker

POLL_TIMEOUT = 60.0
"""Seconds to wait for the Kafka consumer to complete a check."""


async def _poll_until_complete(
    client: AsyncClient, location: str
) -> dict[str, Any]:
    """Poll a check until the Kafka consumer completes it."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + POLL_TIMEOUT
    while True:
        response = await client.get(location)
        assert response.status_code == 200
        data = response.json()
        if data["status"] == "complete":
            return data
        if loop.time() > deadline:
            pytest.fail(
                f"Check at {location} did not complete within"
                f" {POLL_TIMEOUT}s; last status was {data['status']!r}"
            )
        await asyncio.sleep(0.2)


@pytest.mark.asyncio
async def test_post_enqueues_and_consumer_executes(
    mock_github: GitHubMocker,
    client: AsyncClient,
) -> None:
    """A submitted check with due URLs is executed via the Kafka
    consumer: polling eventually shows the check complete with correct
    per-URL statuses.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gone":
            return httpx.Response(404)
        return httpx.Response(200)

    mock_github.router.route(host="example.com").mock(side_effect=handler)

    # A submission with due URLs is enqueued and executed.
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "origin_base_url": "https://sqr-000.lsst.io",
            "is_default_version": True,
            "urls": [
                {"url": "https://example.com/ok", "origin_paths": ["index"]},
                {"url": "https://example.com/gone", "origin_paths": ["index"]},
            ],
        },
    )
    assert response.status_code == 202
    location = response.headers["Location"]

    data = await _poll_until_complete(client, location)
    assert data["date_completed"] is not None
    assert data["summary"] == {
        "pending": 0,
        "ok": 1,
        "redirected": 0,
        "failing": 0,
        "broken": 1,
        "unsupported": 0,
    }
    results = {u["url"]: u for u in data["urls"]}
    ok = results["https://example.com/ok"]
    assert ok["status"] == "ok"
    assert ok["status_code"] == 200
    assert ok["checked_at"] is not None
    gone = results["https://example.com/gone"]
    assert gone["status"] == "broken"
    assert gone["status_code"] == 404
    assert gone["checked_at"] is not None


@pytest.mark.asyncio
async def test_recheck_message_advances_ladder(
    mock_github: GitHubMocker,
    client: AsyncClient,
) -> None:
    """A RecheckUrlsMessage re-checks the given URLs and advances their
    statuses through the retry ladder: a failing link past the broken
    threshold becomes broken.
    """
    mock_github.router.route(host="recheck.example.com").mock(
        return_value=httpx.Response(404)
    )

    url = "https://recheck.example.com/gone"
    now = datetime.now(tz=UTC).replace(microsecond=0)
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    try:
        session = await create_async_session(engine)
        try:
            store = LinkCheckStore(
                session=session, logger=structlog.get_logger("test")
            )
            async with session.begin():
                # A failing URL past the broken threshold, due for its
                # scheduled recheck and still referenced by an origin.
                await store.upsert_url_state(
                    LinkState(
                        url=url,
                        status=LinkStatus.failing,
                        checked_at=now - timedelta(hours=1),
                        last_ok_at=now - timedelta(days=4),
                        failing_since=now - timedelta(days=3),
                        failure_count=3,
                        status_code=404,
                        error="404 Not Found",
                        next_check_at=now - timedelta(minutes=5),
                    )
                )
                ids = await store.upsert_checked_urls([url])
                await store.replace_origin_occurrences(
                    origin_base_url="https://sqr-000.lsst.io",
                    occurrences=[UrlOccurrence(url=url, origin_path="index")],
                )
        finally:
            await session.close()
    finally:
        await engine.dispose()

    message = RecheckUrlsMessageV1(url_ids=[ids[url]])
    await kafka_router.broker.publish(
        message.model_dump(mode="json"), topic=config.linkcheck_kafka_topic
    )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + POLL_TIMEOUT
    while True:
        response = await client.get("/ook/linkcheck/urls", params={"url": url})
        assert response.status_code == 200
        data = response.json()
        if data["status"] == "broken":
            break
        if loop.time() > deadline:
            pytest.fail(
                f"URL {url} did not become broken within {POLL_TIMEOUT}s;"
                f" last status was {data['status']!r}"
            )
        await asyncio.sleep(0.2)
    assert data["failure_count"] == 4
