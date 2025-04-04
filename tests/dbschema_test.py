"""Test the database schema."""

import subprocess

import pytest
from safir.database import create_database_engine, drop_database

from ook.config import config
from ook.dbschema import Base


@pytest.mark.asyncio
async def test_schema() -> None:
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    await drop_database(engine, Base.metadata)
    await engine.dispose()
    subprocess.run(["alembic", "upgrade", "head"], check=True)  # noqa: ASYNC221
    subprocess.run(["alembic", "check"], check=True)  # noqa: ASYNC221
