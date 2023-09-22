"""Pytest configuration for the ``ook`` app."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from unittest.mock import Mock

import pytest
import pytest_asyncio
import structlog
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import AsyncClient

from ook import main
from ook.factory import Factory

from .support.algoliasearch import MockSearchClient, patch_algoliasearch
from .support.kafkaproducer import patch_aiokafkaproducer
from .support.schemamanager import (
    MockPydanticSchemaManager,
    patch_schema_manager,
)


@pytest.fixture
def mock_algoliasearch() -> Iterator[MockSearchClient]:
    """Return a mock Algolia SearchClient for testing."""
    yield from patch_algoliasearch()


@pytest.fixture
def mock_schema_manager() -> Iterator[MockPydanticSchemaManager]:
    """Return a mock PydanticSchemaManager for testing."""
    yield from patch_schema_manager()


@pytest.fixture
def mock_kafka_producer() -> Iterator[Mock]:
    """Return a mock KafkaProducer for testing."""
    yield from patch_aiokafkaproducer()


@pytest_asyncio.fixture
async def http_client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient() as client:
        yield client


@pytest_asyncio.fixture
async def app(
    mock_kafka_producer: Mock,
    mock_schema_manager: MockPydanticSchemaManager,
    mock_algoliasearch: MockSearchClient,
) -> AsyncIterator[FastAPI]:
    """Return a configured test application.

    Wraps the application in a lifespan manager so that startup and shutdown
    events are sent during test execution.
    """
    async with LifespanManager(main.app):
        yield main.app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Return an ``httpx.AsyncClient`` configured to talk to the test app."""
    async with AsyncClient(app=app, base_url="https://example.com/") as client:
        yield client


@pytest_asyncio.fixture
async def factory(
    mock_kafka_producer: Mock,
    mock_schema_manager: MockPydanticSchemaManager,
    mock_algoliasearch: MockSearchClient,
) -> AsyncIterator[Factory]:
    """Return a configured ``Factory``."""
    logger = structlog.get_logger("ook")
    async with Factory.create_standalone(logger=logger) as factory:
        yield factory
        await factory.aclose()
