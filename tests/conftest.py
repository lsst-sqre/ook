"""Pytest configuration for the ``ook`` app."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence

import pytest
import pytest_asyncio
import structlog
from asgi_lifespan import LifespanManager
from faststream_fastapi import FastStreamAPI
from httpx import ASGITransport, AsyncClient
from safir.database import create_database_engine

from ook import main
from ook.config import config
from ook.factory import Factory
from ook.services import intersphinx as intersphinx_service
from ook.services.linkcheck import _urlchecker

from .support.algoliasearch import MockSearchClient, patch_algoliasearch
from .support.database import reset_database_for_test
from .support.github import GitHubMocker


@pytest.fixture(autouse=True, scope="session")
def _patched_ssrf_guard_dns() -> Iterator[None]:
    """Resolve every hostname to a public address so the SSRF guards in the
    link-check URL checker and the intersphinx cache never perform real DNS
    lookups.

    The application's Kafka consumer executes link checks in the
    background of any test that submits them, so DNS must resolve
    deterministically (and the subsequent HTTP request is then handled,
    or rejected, by respx) regardless of network availability. The
    intersphinx cache's guard likewise resolves origin hosts before a
    cold-miss fetch, so it must resolve deterministically too.

    This is session-scoped because the ``UrlChecker`` singleton captures
    ``_default_resolve_host`` when the shared application lifespan
    constructs the process context, so the patch must be in place before
    the session-scoped app starts and stay in place for the whole session.
    """
    monkeypatch = pytest.MonkeyPatch()

    async def resolve_host(host: str) -> Sequence[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(_urlchecker, "_default_resolve_host", resolve_host)
    monkeypatch.setattr(
        intersphinx_service, "_default_resolve_host", resolve_host
    )
    yield
    monkeypatch.undo()


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


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _app_lifespan(
    _patched_ssrf_guard_dns: None,
) -> AsyncIterator[FastStreamAPI]:
    """Start the test application once per pytest session.

    FastStreamAPI starts the Kafka broker (producer plus consumer-group
    join) before entering the app's own lifespan; that hand-shake costs
    seconds, so it is paid once per session rather than once per test.
    Test isolation comes from the per-test database reset in the ``app``
    fixture, and per-test HTTP mocking (respx) intercepts at the transport
    layer so it works with the long-lived clients created here.
    """
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    await reset_database_for_test(engine)
    await engine.dispose()
    async with LifespanManager(main.app):
        yield main.app


@pytest_asyncio.fixture
async def app(
    _app_lifespan: FastStreamAPI,
    mock_algoliasearch: MockSearchClient,
    mock_github: GitHubMocker,
) -> AsyncIterator[FastStreamAPI]:
    """Return the running test application with an empty database.

    The application (broker, database pool, HTTP clients) is shared across
    the session; each test starts from an empty, Alembic-stamped database.
    """
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    await reset_database_for_test(engine)
    await engine.dispose()
    yield _app_lifespan


@pytest_asyncio.fixture
async def client(app: FastStreamAPI) -> AsyncIterator[AsyncClient]:
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
    await reset_database_for_test(engine)
    async with Factory.create_standalone(
        logger=logger, engine=engine
    ) as factory:
        yield factory
    await engine.dispose()
