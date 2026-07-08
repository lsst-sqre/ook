"""Test the database schema."""

import os
import subprocess

import pytest
import sqlalchemy as sa
import structlog
from safir.database import (
    create_database_engine,
    drop_database,
    initialize_database,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from ook.config import config
from ook.dbschema import Base


async def _init_database() -> AsyncEngine:
    """Build a fresh schema from the models and return the engine."""
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    logger = structlog.get_logger("ook")
    await initialize_database(engine, logger, schema=Base.metadata, reset=True)
    return engine


async def _seed_relation_endpoints(engine: AsyncEngine) -> None:
    """Insert a source resource, an internal target resource, and an external
    reference to serve as the endpoints of resource relations.
    """
    async with engine.begin() as conn:
        for resource_id, title in ((1000, "Source"), (2000, "Target")):
            await conn.execute(
                sa.text(
                    "INSERT INTO resource "
                    "(id, date_created, date_updated, title) "
                    "VALUES (:id, now(), now(), :title)"
                ),
                {"id": resource_id, "title": title},
            )
        await conn.execute(
            sa.text(
                "INSERT INTO external_reference (id, title) "
                "VALUES (:id, :title)"
            ),
            {"id": 9001, "title": "External"},
        )


@pytest.mark.asyncio
async def test_duplicate_internal_relation_rejected() -> None:
    """A second internal relation with the same source, target, and type is
    rejected by the partial unique index.

    The old whole-row unique constraint let this through because the always
    NULL ``related_external_ref_id`` column made every row distinct under
    PostgreSQL's default ``NULLS DISTINCT`` semantics.
    """
    engine = await _init_database()
    try:
        await _seed_relation_endpoints(engine)
        insert = sa.text(
            "INSERT INTO resource_relation "
            "(source_resource_id, related_resource_id, relation_type) "
            "VALUES (:source, :related, :rtype)"
        )
        params = {"source": 1000, "related": 2000, "rtype": "Cites"}
        async with engine.begin() as conn:
            await conn.execute(insert, params)
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(insert, params)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_external_relation_rejected() -> None:
    """A second external-reference relation with the same source, target, and
    type is rejected by its partial unique index (the mirror of the internal
    edge, which the old whole-row constraint likewise failed to enforce).
    """
    engine = await _init_database()
    try:
        await _seed_relation_endpoints(engine)
        insert = sa.text(
            "INSERT INTO resource_relation "
            "(source_resource_id, related_external_ref_id, relation_type) "
            "VALUES (:source, :ext, :rtype)"
        )
        params = {"source": 1000, "ext": 9001, "rtype": "References"}
        async with engine.begin() as conn:
            await conn.execute(insert, params)
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(insert, params)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expected_indexes_exist() -> None:
    """The partial unique indexes and the previously missing supporting
    indexes are all present in the built schema.
    """
    engine = await _init_database()
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                sa.text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = 'public'"
                )
            )
            index_names = {row[0] for row in rows}
        expected = {
            "uq_resource_relation_internal",
            "uq_resource_relation_external",
            "idx_resource_relation_related_resource",
            "idx_resource_relation_related_external_ref",
            "idx_resource_relation_source_type",
            "idx_contributor_author",
        }
        assert expected <= index_names
        # The dead whole-row unique constraint must no longer exist.
        assert "uq_resource_relation" not in index_names
    finally:
        await engine.dispose()


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
