"""Database reset helpers for tests.

Creating the full schema with ``initialize_database(..., reset=True)`` per
test (drop_all + create_all) is expensive. Instead, the schema is created
(and Alembic-stamped) once per pytest process and each test starts by
truncating every table, which preserves the empty-database isolation
guarantee at a fraction of the cost. ``TRUNCATE ... RESTART IDENTITY``
also resets sequences so tests that assert specific autoincrement IDs
behave identically to a freshly created schema.

The ``alembic_version`` table is deliberately not truncated: the
application lifespan checks ``is_database_current`` at startup, so the
stamp applied at schema-creation time must survive between tests.
"""

from __future__ import annotations

import structlog
from safir.database import initialize_database, stamp_database_async
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ook.dbschema import Base

__all__ = ["reset_database_for_test"]

_schema_ready = False
"""Whether this pytest process has already created the schema."""


async def reset_database_for_test(engine: AsyncEngine) -> None:
    """Give the current test an empty, Alembic-stamped database.

    The first call in a pytest process performs the full
    ``initialize_database`` (drop_all + create_all) and stamps the Alembic
    version. Subsequent calls truncate all schema tables (restarting
    identity sequences) instead, which is much faster and equivalent for
    test isolation.

    Parameters
    ----------
    engine
        Database engine to use.
    """
    global _schema_ready
    if not _schema_ready:
        logger = structlog.get_logger("ook")
        await initialize_database(
            engine, logger, schema=Base.metadata, reset=True
        )
        await stamp_database_async(engine)
        _schema_ready = True
        return

    tables = ", ".join(
        f'"{table.name}"' for table in Base.metadata.sorted_tables
    )
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
