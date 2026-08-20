"""Integration tests for the per-test Kafka drain barrier.

`tests.support.kafka` is unit-tested in ``tests/kafka_drain_test.py``;
these tests check that it is wired to the real broker -- that the tracker
is armed for every running subscriber, and that draining it really does
wait for a handler execution the test left in flight.
"""

from __future__ import annotations

import httpx
import pytest
from faststream_fastapi import FastStreamAPI
from httpx import AsyncClient

from ook.config import config
from ook.kafkabroker import kafka_broker

from .support.github import GitHubMocker
from .support.kafka import kafka_work_tracker, running_subscriptions

PINNED_IP = "93.184.216.34"
"""The address every host resolves to under the patched SSRF guard, and
therefore the host of the outbound request respx sees.
"""


@pytest.mark.asyncio
async def test_barrier_tracks_every_running_subscriber(
    app: FastStreamAPI,
) -> None:
    """Every topic the application consumes is drainable.

    A topic missing here would leave its handlers running into the next
    test with the barrier reporting itself idle.
    """
    subscriptions = running_subscriptions(kafka_broker)

    assert subscriptions == {
        config.ingest_kafka_topic: ["HandleLtdDocumentIngest"],
        config.linkcheck_kafka_topic: ["HandleLinkcheckExecution"],
    }


@pytest.mark.asyncio
async def test_drain_completes_a_check_left_in_flight(
    mock_github: GitHubMocker,
    client: AsyncClient,
) -> None:
    """``POST /ook/linkcheck/checks`` answers 202 while the Kafka consumer
    is still executing the check. Draining -- what the ``app`` fixture does
    at teardown -- makes that execution finish here, inside the test that
    published it, rather than during whichever test runs next.
    """
    route = mock_github.router.route(
        host=PINNED_IP, headers={"Host": "example.com"}
    ).mock(return_value=httpx.Response(200))

    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "origin_base_url": "https://sqr-000.lsst.io",
            "is_default_version": True,
            "urls": [
                {"url": "https://example.com/drain", "origin_paths": ["index"]}
            ],
        },
    )
    assert response.status_code == 202
    location = response.headers["Location"]

    await kafka_work_tracker.drain()

    # No polling: the barrier is the synchronization point.
    assert route.called, "the handler's outbound fetch ran before the barrier"
    data = (await client.get(location)).json()
    assert data["status"] == "complete"
    assert data["summary"]["ok"] == 1
