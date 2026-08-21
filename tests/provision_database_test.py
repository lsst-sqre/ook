"""Tests for the shared test-database provisioning helper.

``tests.support.database.provision_database`` is the one place a test-suite
database is created: the pytest-xdist worker shim calls it for each worker's
database and ``ddl_database_url`` calls it for the DDL database. These tests
pin down what both of them rely on, so a change to the helper cannot quietly
hand one caller a database the other could not use.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import asyncpg
import pytest

from .support.database import provision_database


async def _connect(url: str) -> asyncpg.Connection:
    """Open a raw asyncpg connection to a database URL from the environment."""
    parts = urlsplit(url)
    return await asyncpg.connect(
        host=parts.hostname,
        port=parts.port,
        user=parts.username,
        password=os.environ["OOK_DATABASE_PASSWORD"],
        database=parts.path.lstrip("/"),
    )


@pytest.mark.asyncio
async def test_provision_database_creates_an_empty_database_with_pg_trgm() -> (
    None
):
    """The helper returns a URL for a fresh database with ``pg_trgm``.

    The extension is what makes this more than a ``CREATE DATABASE``: the
    schema's trigram indexes need it, so a worker database or DDL database
    provisioned without it fails at ``create_all`` rather than here.
    """
    current_url = os.environ["OOK_DATABASE_URL"]
    current = urlsplit(current_url).path.lstrip("/")
    name = f"{current}_provision_probe"

    url = await provision_database(name)
    try:
        assert urlsplit(url).path.lstrip("/") == name

        conn = await _connect(url)
        try:
            extensions = await conn.fetchval(
                "SELECT count(*) FROM pg_extension WHERE extname = 'pg_trgm'"
            )
            await conn.execute("CREATE TABLE probe (id integer)")
        finally:
            await conn.close()
        assert extensions == 1

        # Provisioning the same name again replaces the database rather than
        # handing back whatever the last caller left in it.
        await provision_database(name)
        conn = await _connect(url)
        try:
            leftover = await conn.fetchval(
                "SELECT to_regclass('public.probe')"
            )
        finally:
            await conn.close()
        assert leftover is None
    finally:
        conn = await _connect(current_url)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            await conn.close()
