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
                "INSERT INTO external_reference (id, title, doi) "
                "VALUES (:id, :title, :doi)"
            ),
            # A dedup key (here a DOI) is required by
            # chk_external_reference_has_key.
            {"id": 9001, "title": "External", "doi": "10.9999/external"},
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
            "uq_external_reference_url",
        }
        assert expected <= index_names
        # The dead whole-row unique constraint must no longer exist.
        assert "uq_resource_relation" not in index_names
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_keyless_external_reference_rejected() -> None:
    """An external reference with no dedup key is rejected by the check
    constraint, while one carrying only a URL is accepted.
    """
    engine = await _init_database()
    try:
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    sa.text(
                        "INSERT INTO external_reference (id, title) "
                        "VALUES (:id, :title)"
                    ),
                    {"id": 9100, "title": "Keyless"},
                )
        # A URL-only reference satisfies the constraint.
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO external_reference (id, title, url) "
                    "VALUES (:id, :title, :url)"
                ),
                {
                    "id": 9101,
                    "title": "URL only",
                    "url": "https://example.org",
                },
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_external_reference_url_rejected() -> None:
    """Two external references with the same URL collide on the partial unique
    index, while multiple NULL-URL references (keyed otherwise) coexist.
    """
    engine = await _init_database()
    try:
        insert_url = sa.text(
            "INSERT INTO external_reference (id, title, url) "
            "VALUES (:id, :title, :url)"
        )
        async with engine.begin() as conn:
            await conn.execute(
                insert_url,
                {"id": 9200, "title": "First", "url": "https://example.org/a"},
            )
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    insert_url,
                    {
                        "id": 9201,
                        "title": "Duplicate",
                        "url": "https://example.org/a",
                    },
                )
        # NULL URLs stay distinct under the partial index (keyed by DOI here).
        async with engine.begin() as conn:
            for ref_id, doi in ((9202, "10.1/x"), (9203, "10.1/y")):
                await conn.execute(
                    sa.text(
                        "INSERT INTO external_reference (id, title, doi) "
                        "VALUES (:id, :title, :doi)"
                    ),
                    {"id": ref_id, "title": "No URL", "doi": doi},
                )
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
