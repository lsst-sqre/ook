"""Pytest configuration for the ``ook`` app."""

from __future__ import annotations

from .support.xdist import isolate_xdist_worker, verify_worker_isolation

# Every import below must come after this call: ook.config snapshots the
# environment into its module-level Configuration() singleton the moment it
# is imported, so the shim has to rewrite that environment first. Each of
# those imports carries its own `noqa: E402` rather than a file-wide one, so
# adding another is a deliberate act; verify_worker_isolation, called after
# them, fails collection if one ever gets ahead of the shim anyway. See
# tests.support.xdist.
isolate_xdist_worker()

from collections.abc import AsyncIterator, Iterator, Sequence  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
import structlog  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402
from faststream_fastapi import FastStreamAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from safir.database import create_database_engine  # noqa: E402

from ook import main  # noqa: E402
from ook.config import config  # noqa: E402
from ook.factory import Factory  # noqa: E402
from ook.kafkabroker import kafka_broker  # noqa: E402
from ook.services import intersphinx as intersphinx_service  # noqa: E402
from ook.services.linkcheck import _urlchecker  # noqa: E402

from .support.algoliasearch import (  # noqa: E402
    MockSearchClient,
    patch_algoliasearch,
)
from .support.database import (  # noqa: E402
    ddl_database_url,
    invalidate_schema,
    reset_database_for_test,
)
from .support.github import GitHubMocker  # noqa: E402
from .support.kafka import (  # noqa: E402
    kafka_work_tracker,
    running_subscriptions,
)

verify_worker_isolation(
    database_url=str(config.database_url),
    ingest_kafka_topic=config.ingest_kafka_topic,
    linkcheck_kafka_topic=config.linkcheck_kafka_topic,
    kafka_consumer_group_id=config.kafka_consumer_group_id,
)


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
async def _rebuild_ddl_schema_after_test() -> AsyncIterator[None]:
    """Rebuild the DDL database's schema after this test, pass or fail.

    Request this from any test that drops the DDL database or structurally
    mutates its schema -- an Alembic rebuild, a data migration that recreates
    foreign keys. Without it the next `tests.support.database.
    reset_database_for_test` truncates whatever the test left behind: an
    Alembic-built or migration-mutated schema on success, and nothing at all
    when the test failed partway through its own rebuild. The teardown here
    runs either way, so the next DDL test sees the canonical ``create_all``
    schema regardless of the order the two ran in.
    """
    url = await ddl_database_url()
    yield
    invalidate_schema(url)


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
