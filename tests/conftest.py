"""Pytest configuration for the ``ook`` app."""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit, urlunsplit


def _isolate_xdist_worker() -> None:
    """Give each pytest-xdist worker its own database and Kafka namespace.

    Under pytest-xdist every worker is a separate process that would
    otherwise share the single testcontainers PostgreSQL database and
    Kafka topics, and the per-test schema reset in the ``app`` and
    ``factory`` fixtures would clobber concurrently running tests. This
    shim runs at conftest import time in each worker process -- before
    ``ook.config`` is imported, so before the module-level
    ``Configuration()`` singleton reads the environment -- and:

    - creates a dedicated PostgreSQL database named after the worker
      (``<base>_gw0``, ...) on the shared container, with the ``pg_trgm``
      extension installed, and points ``OOK_DATABASE_URL`` at it; and
    - namespaces the Kafka topics and consumer group per worker so each
      worker's FastStream broker publishes and consumes independently.

    In a non-xdist (serial) run ``PYTEST_XDIST_WORKER`` is unset and this
    is a no-op: the suite runs against the base database and topics
    exactly as before.
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    if worker_id is None:
        return

    import asyncpg  # noqa: PLC0415

    url = urlsplit(os.environ["OOK_DATABASE_URL"])
    base_database = url.path.lstrip("/")
    worker_database = f"{base_database}_{worker_id}"
    password = os.environ["OOK_DATABASE_PASSWORD"]

    async def _create_worker_database() -> None:
        conn = await asyncpg.connect(
            host=url.hostname,
            port=url.port,
            user=url.username,
            password=password,
            database=base_database,
        )
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{worker_database}"')
            await conn.execute(f'CREATE DATABASE "{worker_database}"')
        finally:
            await conn.close()
        conn = await asyncpg.connect(
            host=url.hostname,
            port=url.port,
            user=url.username,
            password=password,
            database=worker_database,
        )
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        finally:
            await conn.close()

    asyncio.run(_create_worker_database())

    os.environ["OOK_DATABASE_URL"] = urlunsplit(
        url._replace(path=f"/{worker_database}")
    )
    os.environ["OOK_INGEST_KAFKA_TOPIC"] = f"ook.ingest.{worker_id}"
    os.environ["OOK_LINKCHECK_KAFKA_TOPIC"] = f"ook.linkcheck.{worker_id}"
    os.environ["OOK_GROUP_ID"] = f"ook-{worker_id}"


_isolate_xdist_worker()

# The imports below must come after the xdist worker isolation shim so
# that ook.config reads the (possibly rewritten) environment.
# ruff: noqa: E402

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
from ook.kafkabroker import kafka_broker
from ook.services import intersphinx as intersphinx_service
from ook.services.linkcheck import _urlchecker

from .support.algoliasearch import MockSearchClient, patch_algoliasearch
from .support.database import reset_database_for_test
from .support.github import GitHubMocker
from .support.kafka import kafka_work_tracker, running_subscriptions


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

    Because the subscribers keep running between tests, the Kafka work a
    test leaves behind has to be drained before the next one starts; the
    tracker armed here is what the ``app`` and ``factory`` fixtures wait on.
    See `tests.support.kafka`.
    """
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    await reset_database_for_test(engine)
    await engine.dispose()
    kafka_work_tracker.install(kafka_broker)
    async with LifespanManager(main.app):
        subscriptions = running_subscriptions(kafka_broker)
        if not subscriptions:
            raise RuntimeError(
                "The application lifespan started no Kafka subscribers, so"
                " the per-test drain barrier would silently do nothing."
            )
        kafka_work_tracker.arm(subscriptions)
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

    Because the broker is never stopped between tests, a Kafka handler left
    over from an earlier test can still hold a transaction open when the reset
    runs. The reset therefore truncates under a short ``lock_timeout`` with a
    bounded retry rather than waiting indefinitely; see
    ``tests.support.database``.

    The teardown drains the Kafka work this test published before returning,
    so no handler is still running when the respx routers this fixture
    depends on are removed or when the next test truncates the database. It
    is a no-op for a test that published nothing. See `tests.support.kafka`.
    """
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    await reset_database_for_test(engine)
    await engine.dispose()
    yield _app_lifespan
    await kafka_work_tracker.drain()


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
    """Return a configured ``Factory`` without setting up a FastAPI app.

    The standalone factory reaches for the same module-level Kafka broker
    the application runs on. When the session-scoped app lifespan has
    already started that broker, ``Factory.create_standalone`` leaves its
    lifecycle alone, so this fixture's teardown cannot close a producer or
    stop subscribers a later app test still needs. Otherwise the factory
    owns the broker and stops it here.

    Like the ``app`` fixture, the teardown drains whatever this test
    published on the shared broker before the respx routers come down. When
    no test in this pytest-xdist worker has started the application, nothing
    is consuming those topics and the drain is a no-op.
    """
    logger = structlog.get_logger("ook")
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    await reset_database_for_test(engine)
    async with Factory.create_standalone(
        logger=logger, engine=engine
    ) as factory:
        yield factory
        await kafka_work_tracker.drain()
    await engine.dispose()
