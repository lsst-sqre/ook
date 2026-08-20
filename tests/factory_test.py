"""Tests for the Kafka broker ownership semantics of ``ook.factory``.

The FastStream application, the CLI, and the test suite's ``factory``
fixture all reach for the same module-level ``ook.kafkabroker.kafka_broker``
singleton. A standalone factory must therefore only manage that broker's
lifecycle when nothing else has started it; stopping a broker it does not
own would close the producer and stop the subscribers of a still-running
application.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import structlog
from faststream.kafka import KafkaBroker
from faststream_fastapi import FastStreamAPI
from httpx import AsyncClient
from safir.database import create_database_engine

from ook.config import config
from ook.factory import Factory
from ook.kafkabroker import kafka_broker
from tests.support.algoliasearch import MockSearchClient
from tests.support.github import GitHubMocker

POLL_TIMEOUT = 60.0
"""Seconds to wait for the Kafka consumer to complete a check."""

PINNED_IP = "93.184.216.34"
"""The address every host resolves to under the patched SSRF guard DNS."""


async def _poll_until_complete(
    client: AsyncClient, location: str
) -> dict[str, Any]:
    """Poll a link check until the Kafka consumer completes it."""
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
async def test_factory_fixture_shares_the_running_broker(
    app: FastStreamAPI, factory: Factory
) -> None:
    """The ``factory`` fixture uses the running application's broker rather
    than a broker of its own.

    This is the first half of the ordering regression: tearing this test's
    ``factory`` fixture down must leave the shared broker usable for the
    round-trip test that follows.
    """
    assert kafka_broker.running
    assert factory._process_context.kafka_broker is kafka_broker


@pytest.mark.asyncio
async def test_shared_broker_round_trips_after_the_factory_fixture(
    mock_github: GitHubMocker, client: AsyncClient
) -> None:
    """A submitted link check is still published and consumed after the
    preceding ``factory``-fixture test was torn down.
    """
    mock_github.router.route(
        host=PINNED_IP, headers={"Host": "example.com"}
    ).mock(return_value=httpx.Response(200))

    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "origin_base_url": "https://sqr-000.lsst.io",
            "is_default_version": True,
            "urls": [
                {"url": "https://example.com/ok", "origin_paths": ["index"]}
            ],
        },
    )
    assert response.status_code == 202

    data = await _poll_until_complete(client, response.headers["Location"])
    assert data["summary"]["ok"] == 1


@pytest.mark.asyncio
async def test_standalone_factory_preserves_a_running_broker(
    app: FastStreamAPI,
) -> None:
    """A standalone factory leaves the producer and the subscribers of an
    already-running shared broker alone.
    """
    assert kafka_broker.running

    engine = create_database_engine(
        config.database_url, config.database_password
    )
    try:
        async with Factory.create_standalone(
            logger=structlog.get_logger("ook"), engine=engine
        ):
            pass
    finally:
        await engine.dispose()

    assert kafka_broker.running
    assert kafka_broker._connection is not None
    assert all(subscriber.running for subscriber in kafka_broker.subscribers)


@pytest.mark.asyncio
async def test_standalone_factory_stops_the_broker_it_started(
    mock_algoliasearch: MockSearchClient,
) -> None:
    """A standalone factory that is the sole owner of its broker -- the CLI
    case -- connects it on entry and stops it on exit.
    """
    broker = KafkaBroker(**config.kafka.to_faststream_params())
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    try:
        async with Factory.create_standalone(
            logger=structlog.get_logger("ook"),
            engine=engine,
            kafka_broker=broker,
        ):
            assert broker._connection is not None
    finally:
        await engine.dispose()

    assert broker._connection is None
