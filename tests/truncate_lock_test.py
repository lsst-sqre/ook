"""Tests for the bounded per-test database truncate.

These run against the dedicated DDL database so the deliberately blocking
transaction can never contend with the session-scoped application.
"""

from __future__ import annotations

import pytest
from safir.database import create_database_engine
from sqlalchemy import text

from ook.config import config

from .support.database import (
    TruncateLockError,
    ddl_database_url,
    reset_database_for_test,
    truncate_all_tables,
)


@pytest.mark.asyncio
async def test_truncate_gives_up_and_names_the_blocker() -> None:
    """A truncate blocked by another session fails loudly instead of hanging.

    Without the ``lock_timeout`` this would wait forever, which is exactly how
    one GitHub Actions run of this suite went silent until the job timeout.
    """
    database_url = await ddl_database_url()
    engine = create_database_engine(database_url, config.database_password)
    blocker = create_database_engine(database_url, config.database_password)
    try:
        await reset_database_for_test(engine)

        async with blocker.connect() as conn:
            # An open transaction holding ACCESS SHARE on any truncated table
            # conflicts with the ACCESS EXCLUSIVE lock TRUNCATE requires.
            await conn.execute(
                text('LOCK TABLE "resource" IN ACCESS SHARE MODE')
            )
            with pytest.raises(TruncateLockError) as excinfo:
                await truncate_all_tables(
                    engine,
                    lock_timeout="150ms",
                    attempts=2,
                    retry_delay=0.01,
                )

        message = str(excinfo.value)
        assert "after 2 attempts" in message
        assert "pid=" in message
        assert "LOCK TABLE" in message

        # Once the blocking transaction is gone the truncate succeeds again,
        # which is why a bounded retry is worth having at all.
        await truncate_all_tables(engine)
    finally:
        await blocker.dispose()
        await engine.dispose()
