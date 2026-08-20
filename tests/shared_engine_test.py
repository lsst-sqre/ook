"""Tests for the session-scoped database engine the test fixtures share.

``tests/conftest.py`` used to build, use, and dispose a database engine in
each of three places -- the session-scoped application lifespan and the
per-test ``app`` and ``factory`` fixtures -- only to reset the database
between tests. They now share one engine per pytest-xdist worker, created
once and disposed once at session teardown. These tests pin down the parts
of that arrangement a later edit could silently undo: that the engine
arrives with a database the application's migration check accepts, that the
``factory`` fixture runs on it instead of one of its own, and that no
per-test fixture disposes it on the way out.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from faststream_fastapi import FastStreamAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ook.factory import Factory


@pytest_asyncio.fixture
async def _shared_pool_watch(
    database_engine: AsyncEngine,
) -> AsyncIterator[None]:
    """Fail the test if a fixture disposes the shared engine at teardown.

    ``AsyncEngine.dispose`` replaces the connection pool with a freshly
    recreated one, so pool identity is what distinguishes an engine that was
    left alone from one that was disposed. Request this through
    ``@pytest.mark.usefixtures``: those fixtures are set up before the ones
    a test takes as arguments, and same-scope fixtures are torn down in
    reverse order of setup, so the check below runs after the fixture under
    test has had its chance to dispose the engine.
    """
    pool = database_engine.sync_engine.pool
    yield
    assert database_engine.sync_engine.pool is pool, (
        "a per-test fixture disposed the session-scoped database engine;"
        " it must be disposed exactly once, at session teardown"
    )


@pytest.mark.asyncio
async def test_shared_engine_arrives_stamped(
    database_engine: AsyncEngine,
) -> None:
    """The engine fixture leaves the schema built and Alembic-stamped.

    The application's lifespan refuses to start against a database that is
    not at the current Alembic revision, and the per-test reset only
    truncates -- it preserves that stamp but never creates it. So the
    fixture that hands out the engine has to leave the database ready before
    anything else asks for it.
    """
    async with database_engine.connect() as connection:
        stamp = await connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )

    assert stamp


@pytest.mark.asyncio
async def test_factory_fixture_runs_on_the_shared_engine(
    factory: Factory, database_engine: AsyncEngine
) -> None:
    """The ``factory`` fixture opens its session on the shared engine.

    Building one per test costs an engine construction, an asyncpg
    handshake, and a pool teardown for a session that a pooled connection
    serves just as well.
    """
    assert factory.db_session.get_bind() is database_engine.sync_engine


@pytest.mark.asyncio
@pytest.mark.usefixtures("_shared_pool_watch")
async def test_app_fixture_leaves_the_shared_engine_alive(
    app: FastStreamAPI,
    database_engine: AsyncEngine,
) -> None:
    """Tearing the ``app`` fixture down must not dispose the shared engine.

    The per-test reset borrows a pooled connection; it does not own the
    pool.
    """
    async with database_engine.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("_shared_pool_watch")
async def test_factory_fixture_leaves_the_shared_engine_alive(
    factory: Factory,
    database_engine: AsyncEngine,
) -> None:
    """Tearing the ``factory`` fixture down must not dispose the shared
    engine either: the session it closes returns its connection to a pool
    that every later test, and the running application, still use.
    """
    async with database_engine.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1
