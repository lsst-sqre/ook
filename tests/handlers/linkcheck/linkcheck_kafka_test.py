"""Integration tests for the async link-check execution path via Kafka.

These tests exercise the full flow against the testcontainers Postgres
and Kafka: ``POST /ook/linkcheck/checks`` enqueues a Kafka message, the
FastStream subscriber executes the check, and polling the check shows it
complete with per-URL results.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

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
            "ltd_slug": "sqr-000",
            "default_branch": True,
            "urls": [
                {"url": "https://example.com/ok", "paths": ["index"]},
                {"url": "https://example.com/gone", "paths": ["index"]},
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
