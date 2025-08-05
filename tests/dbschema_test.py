"""Test the database schema."""

import os
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

    # Run alembic upgrade
    result = subprocess.run(  # noqa: ASYNC221
        ["alembic", "upgrade", "head"],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ,
    )
    if result.returncode != 0:
        print(f"alembic upgrade failed with return code {result.returncode}")
        print(f"stdout: {result.stdout}")
        print(f"stderr: {result.stderr}")
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
        )

    # Run alembic check
    result = subprocess.run(  # noqa: ASYNC221
        ["alembic", "check"],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ,
    )
    if result.returncode != 0:
        print(f"alembic check failed with return code {result.returncode}")
        print(f"stdout: {result.stdout}")
        print(f"stderr: {result.stderr}")
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
        )
