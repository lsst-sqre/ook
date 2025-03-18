"""Pytest configuration for the ``ook`` app."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
import structlog
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from safir.database import create_database_engine, initialize_database

from ook import main
from ook.config import config
from ook.dbschema import Base
from ook.factory import Factory

from .support.algoliasearch import MockSearchClient, patch_algoliasearch
from .support.github import GitHubMocker


@pytest.fixture
def mock_algoliasearch() -> Iterator[MockSearchClient]:
    """Return a mock Algolia SearchClient for testing."""
    yield from patch_algoliasearch()


@pytest.fixture
def mock_github() -> Iterator[GitHubMocker]:
    github_mocker = GitHubMocker()
    with github_mocker.router:
        yield github_mocker


@pytest_asyncio.fixture
async def http_client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient() as client:
        yield client


@pytest_asyncio.fixture
async def app(
    mock_algoliasearch: MockSearchClient,
    mock_github: GitHubMocker,
) -> AsyncIterator[FastAPI]:
    """Return a configured test application.

    Wraps the application in a lifespan manager so that startup and shutdown
    events are sent during test execution.
    """
    logger = structlog.get_logger("ook")
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    await initialize_database(engine, logger, schema=Base.metadata, reset=True)
    await engine.dispose()
    async with LifespanManager(main.app):
        yield main.app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Return an ``httpx.AsyncClient`` configured to talk to the test app."""
    async with AsyncClient(
        base_url="https://example.com/", transport=ASGITransport(app=app)
    ) as client:
        yield client


@pytest_asyncio.fixture
async def factory(
    mock_algoliasearch: MockSearchClient,
    mock_github: GitHubMocker,
) -> AsyncIterator[Factory]:
    """Return a configured ``Factory`` without setting up a FastAPI app."""
    logger = structlog.get_logger("ook")
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    await initialize_database(engine, logger, schema=Base.metadata, reset=True)
    async with Factory.create_standalone(
        logger=logger, engine=engine
    ) as factory:
        yield factory
        await factory.aclose()
    await engine.dispose()
