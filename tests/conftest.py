"""Pytest fixtures and test helper functions."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import AsyncGenerator, Callable

import aiohttp
import pytest
from aiokafka import AIOKafkaProducer
from kafkit.registry.aiohttp import RegistryApi
from kafkit.registry.manager import RecordNameSchemaManager


@pytest.fixture
async def http_session() -> AsyncGenerator[aiohttp.ClientSession, None]:
    """Pytest fixture for an aiohttp ClientSession.

    Don't confuse this fixture with aiohttp's
    `~aiohttp.pytest_plugin.test_utils.TestClient`, which is a client
    specifically designed for interacting with your app's HTTP endpoints.
    **This** is the general-purpose HTTP session for interacting with other
    HTTP services.

    Yields
    ------
    aiohttp.ClientSession
        An aiohttp ClientSession that can be used by test code to interact
        with HTTP services other than your own app.
    """
    async with aiohttp.ClientSession() as session:
        yield session


@pytest.fixture
def registry_api(http_session: aiohttp.ClientSession) -> RegistryApi:
    """Pytest fixture for an aiohttp-based Confluent Schema Registry API
    client.

    Returns
    -------
    kafkit.registry.aiohttp.RegistryApi
        The aiohttp-based Confluent Schema Registry API client.

    Notes
    -----
    This fixture relies upon the Schema Registry's URL being set through the
    ``SAFIR_SCHEMA_REGISTRY_URL`` environment variable. This should be the
    same environment variable that your app uses to configure its Schema
    Registry client.
    """
    return RegistryApi(
        session=http_session, url=str(os.getenv("SAFIR_SCHEMA_REGISTRY_URL")),
    )


@pytest.fixture
async def schema_manager(registry_api: RegistryApi) -> RecordNameSchemaManager:
    """Pytest fixture for a RecordNameSchemaManager.

    Returns
    -------
    kafkit.registry.manager.RecordNameSchemaManager
        A schema manager connected to a Confluent Schema Registry (through
        the `registry_api` fixture). The manager pre-registers Avro
        schema used by producers in test code.

    See also
    --------
    registry_api

    Notes
    -----
    This fixture automatically registers schemas located in the project's
    ``tests/data/avro_schemas`` directory. These Avro Schemas are ones that
    are *consumed* by Ook; test code produces messages with these schemas so
    that ook can consume those messages.

    This fixture uses the `registry_api` fixture.
    """
    schema_manager = RecordNameSchemaManager(
        root=Path(__file__).parent / "data" / "avro_schemas",
        registry=registry_api,
    )
    await schema_manager.register_schemas()

    return schema_manager


@pytest.fixture
async def producer() -> AsyncGenerator[AIOKafkaProducer, None]:
    """Pytest fixture for a Kafka producer.

    Yields
    ------
    aiokafka.AIOKafkaProducer
        The AIOKafkaProducer, which is already "started" by awaiting its
        `~aiokafka.AIOKafkaProducer.start` method.

    Notes
    -----
    This producer uses the "SAFIR_KAFKA_BROKER_URL" environment variable to
    connect to the Kafka brokers. This is the same environment variable that
    your app already uses to connect to Kafka brokers.

    This producer assumes that the Kafka security protocol is ``PLAINTEXT``.
    """
    producer = AIOKafkaProducer(
        loop=asyncio.get_running_loop(),
        bootstrap_servers=str(os.getenv("SAFIR_KAFKA_BROKER_URL")),
        security_protocol="PLAINTEXT",
    )
    await producer.start()
    yield producer

    await producer.stop()


async def async_test_until(
    predicate: Callable, *, timeout: float, period: float = 0.25
) -> bool:
    """Poll a predicate within a timeout period.

    Parameters
    ----------
    predicate : callable
        The predicate function returns `True` if a condition succeeds and
        `False` otherwise. This function should be structured as a closure
        so that it takes no arguments.
    timeout : `float`
        The timeout period in seconds. If the ``predicate`` does not return
        `True` within the timeout, this function return False.
    period : `float`
        The polling period.

    Returns
    -------
    bool
        `True` if the ``predicate`` is `True` within the ``timeout`` period.
        `False` otherwise.

    Examples
    --------
    In this example, the ``predicate`` is testing that a random number is less
    than a threshold. The threshold is defined outside the predicate, but
    the predicate includes the threshold in its closure. If a random number is
    not polled lower than the threshold within the 10 second timeout, the
    assertion fails.

    .. code-block:: python

       import random

       threshold = 0.1

       assert await async_test_until(
            lambda: random.random() < threshold,
            timeout=10.
       )
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        else:
            await asyncio.sleep(period)
    return False
