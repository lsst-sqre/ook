"""Pytest configuration for the ``ook`` app."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
import structlog
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from faststream.kafka.fastapi import KafkaRouter
from faststream.security import BaseSecurity
from httpx import AsyncClient
from testcontainers.kafka import KafkaContainer

import ook.kafkarouter
from ook.config import config
from ook.factory import Factory
from ook.main import create_app

from .support.algoliasearch import MockSearchClient, patch_algoliasearch


@pytest.fixture
def mock_algoliasearch() -> Iterator[MockSearchClient]:
    """Return a mock Algolia SearchClient for testing."""
    yield from patch_algoliasearch()


@pytest_asyncio.fixture
async def kafka_broker_url() -> AsyncIterator[str]:
    """Return the URL of a running Kafka broker."""
    with KafkaContainer().with_kraft() as kafka:
        yield kafka.get_bootstrap_server()


@pytest_asyncio.fixture
async def http_client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient() as client:
        yield client


@pytest_asyncio.fixture
async def app(
    mock_algoliasearch: MockSearchClient,
    kafka_broker_url: str,
) -> AsyncIterator[FastAPI]:
    """Return a configured test application.

    Wraps the application in a lifespan manager so that startup and shutdown
    events are sent during test execution.
    """
    print(kafka_broker_url)
    os.environ["KAFKA_BOOTSTRAP_SERVERS"] = kafka_broker_url
    # Reset the Kafka configuration with new env vars
    # This really is the recommended way to live reload a Pydantic BaseSettings
    # https://docs.pydantic.dev/latest/concepts/pydantic_settings/#in-place-reloading
    config.kafka.__init__()  # type: ignore [misc]
    # Reset the Kafka router with the new configuration
    ook.kafkarouter.kafka_security = BaseSecurity(
        ssl_context=config.kafka.ssl_context
    )
    ook.kafkarouter.kafka_router = KafkaRouter(
        config.kafka.bootstrap_servers, security=ook.kafkarouter.kafka_security
    )
    app = create_app()
    async with LifespanManager(app):
        yield app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Return an ``httpx.AsyncClient`` configured to talk to the test app."""
    async with AsyncClient(app=app, base_url="https://example.com/") as client:
        yield client


@pytest_asyncio.fixture
async def factory(
    mock_algoliasearch: MockSearchClient,
    kafka_broker_url: str,
) -> AsyncIterator[Factory]:
    """Return a configured ``Factory`` without setting up a FastAPI app."""
    logger = structlog.get_logger("ook")
    config.kafka.bootstrap_servers = kafka_broker_url
    async with Factory.create_standalone(logger=logger) as factory:
        yield factory
        await factory.aclose()
