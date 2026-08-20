"""Tests for how the per-test database reset handles a stale schema.

``reset_database_for_test`` remembers which databases this pytest process has
built a schema in and truncates them from then on. Tests that run DDL against
the dedicated DDL database can invalidate that memory underneath it, so these
tests pin down both halves of the recovery: the explicit
``invalidate_schema`` handshake, and the fallback for a schema that vanished
without one.

They run against the DDL database for the same reason the other DDL tests do:
dropping and rebuilding a schema must never contend with the session-scoped
application's connection pool. See ``tests.support.database``.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from safir.database import (
    create_database_engine,
    drop_database,
    initialize_database,
)
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.stdlib import BoundLogger

from ook.config import config
from ook.dbschema import Base

from .support import database as database_support
from .support.database import (
    ddl_database_url,
    invalidate_schema,
    reset_database_for_test,
)


@pytest.mark.asyncio
async def test_reset_rebuilds_a_dropped_schema() -> None:
    """A schema dropped behind the reset's back is rebuilt, not truncated.

    ``tests/dbschema_test.py::test_schema`` drops the DDL database and rebuilds
    it with Alembic. If that rebuild fails -- exactly the case that test exists
    to catch -- the database is left empty while still marked ready, and every
    later DDL-database test would die in ``truncate_all_tables`` with
    ``UndefinedTable``, burying the real failure under a cascade of misleading
    ones.
    """
    engine = create_database_engine(
        await ddl_database_url(), config.database_password
    )
    try:
        await reset_database_for_test(engine)
        assert engine.url.database in database_support._schema_ready

        await drop_database(engine, Base.metadata)
        # The stale entry is deliberately left in place: this is the state a
        # failed Alembic rebuild leaves behind.
        assert engine.url.database in database_support._schema_ready

        await reset_database_for_test(engine)

        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT count(*) FROM "resource"'))
            stamps = (
                await conn.execute(
                    sa.text("SELECT count(*) FROM alembic_version")
                )
            ).scalar_one()
        # The rebuild re-stamps Alembic, which the application lifespan's
        # is_database_current check requires.
        assert stamps == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_invalidate_schema_forces_a_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After invalidation the next reset rebuilds instead of truncating.

    This is what keeps a schema an Alembic upgrade built, or a data migration
    structurally mutated, from leaking into later tests: they get the canonical
    ``create_all`` schema back regardless of the order they run in.
    """
    engine = create_database_engine(
        await ddl_database_url(), config.database_password
    )
    try:
        await reset_database_for_test(engine)

        initializations = 0

        async def counting_initialize(
            engine: AsyncEngine,
            logger: BoundLogger,
            *,
            schema: MetaData,
            reset: bool = False,
        ) -> None:
            nonlocal initializations
            initializations += 1
            await initialize_database(
                engine, logger, schema=schema, reset=reset
            )

        monkeypatch.setattr(
            database_support, "initialize_database", counting_initialize
        )

        # A database this process already built is truncated, not rebuilt.
        await reset_database_for_test(engine)
        assert initializations == 0

        invalidate_schema(await ddl_database_url())
        await reset_database_for_test(engine)
        assert initializations == 1

        # And the rebuild is remembered, so the reset after it truncates again.
        await reset_database_for_test(engine)
        assert initializations == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_invalidate_schema_ignores_unknown_databases() -> None:
    """Invalidating a database this process never built is a no-op.

    A fixture that invalidates on teardown runs even when the test it wraps
    failed before touching the database, so the helper has to tolerate a
    database it has never heard of, and must leave the others alone.
    """
    engine = create_database_engine(
        await ddl_database_url(), config.database_password
    )
    try:
        await reset_database_for_test(engine)
        ready = set(database_support._schema_ready)

        invalidate_schema("postgresql+asyncpg://ook@127.0.0.1:5432/nonesuch")

        assert database_support._schema_ready == ready
    finally:
        await engine.dispose()
