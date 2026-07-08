"""Integration test for the one-time resource-ID re-mint migration."""

from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
import structlog
from safir.database import create_database_engine, initialize_database
from sqlalchemy.ext.asyncio import AsyncEngine

from ook.config import config
from ook.dbschema import Base
from ook.domain.base32id import RESOURCE_ID_EPOCH, RESOURCE_ID_RANDOM_BITS

_MIGRATION_GLOB = "alembic/versions/*_remint_resource_ids_time_ordered.py"


def _load_migration_module() -> ModuleType:
    """Load the re-mint migration module by file path.

    Alembic revision files are not importable as normal modules (their names
    begin with a date), so load it directly to reach ``remint_resource_ids``.
    """
    repo_root = Path(__file__).resolve().parents[2]
    path = next(repo_root.glob(_MIGRATION_GLOB))
    spec = importlib.util.spec_from_file_location("_remint_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _seed(engine: AsyncEngine) -> dict[str, int]:
    """Seed resources (with a millisecond tie), documents, contributors, and
    relations. Returns the original resource IDs keyed by title.
    """

    def created(ms: int) -> object:
        return RESOURCE_ID_EPOCH + timedelta(milliseconds=ms)

    # Titles map to (old_id, created_ms). "B" and "D" share a millisecond, so
    # their relative order must be broken deterministically by the old id. Old
    # ids are deliberately out of creation order to prove the re-mint reorders.
    resources = {
        "A": (500, 3000),
        "B": (100, 1000),
        "C": (300, 2000),
        "D": (200, 1000),
    }
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO external_reference (id, title) "
                "VALUES (:id, :title)"
            ),
            {"id": 9001, "title": "External"},
        )
        for title, (old_id, ms) in resources.items():
            await conn.execute(
                sa.text(
                    "INSERT INTO resource "
                    "(id, resource_class, date_created, date_updated, title) "
                    "VALUES (:id, :cls, :created, :created, :title)"
                ),
                {
                    "id": old_id,
                    "cls": "document" if title in {"A", "C"} else None,
                    "created": created(ms),
                    "title": title,
                },
            )
        # Documents A and C are polymorphic subclass rows keyed on resource.id.
        for title, series, handle, number in (
            ("A", "SQR", "SQR-001", 1),
            ("C", "DMTN", "DMTN-002", 2),
        ):
            await conn.execute(
                sa.text(
                    "INSERT INTO document_resource "
                    "(id, series, handle, number) "
                    "VALUES (:id, :series, :handle, :number)"
                ),
                {
                    "id": resources[title][0],
                    "series": series,
                    "handle": handle,
                    "number": number,
                },
            )
        # Contributors on A and B.
        for cid, title in ((1, "A"), (2, "B")):
            await conn.execute(
                sa.text(
                    'INSERT INTO contributor (id, resource_id, "order", role) '
                    "VALUES (:id, :resource_id, 0, :role)"
                ),
                {
                    "id": cid,
                    "resource_id": resources[title][0],
                    "role": "Creator",
                },
            )
        # An internal relation (A -> C) and an external relation (B -> ext).
        await conn.execute(
            sa.text(
                "INSERT INTO resource_relation "
                "(id, source_resource_id, related_resource_id, relation_type) "
                "VALUES (:id, :source, :related, :rtype)"
            ),
            {
                "id": 1,
                "source": resources["A"][0],
                "related": resources["C"][0],
                "rtype": "Cites",
            },
        )
        await conn.execute(
            sa.text(
                "INSERT INTO resource_relation "
                "(id, source_resource_id, related_external_ref_id, "
                "relation_type) VALUES (:id, :source, :ext, :rtype)"
            ),
            {
                "id": 2,
                "source": resources["B"][0],
                "ext": 9001,
                "rtype": "References",
            },
        )
    return {title: old_id for title, (old_id, _) in resources.items()}


@pytest.mark.asyncio
async def test_remint_reorders_ids_and_preserves_fks() -> None:
    """Re-mint IDs in date_created order while keeping FKs intact."""
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    logger = structlog.get_logger("ook")
    await initialize_database(engine, logger, schema=Base.metadata, reset=True)

    old_ids = await _seed(engine)
    migration = _load_migration_module()

    async with engine.begin() as conn:
        await conn.run_sync(migration.remint_resource_ids)

    try:
        async with engine.connect() as conn:
            new_ids = {
                title: resource_id
                for resource_id, title in (
                    await conn.execute(
                        sa.text("SELECT id, title FROM resource")
                    )
                ).all()
            }

            # Every ID was re-minted away from its original value.
            assert set(new_ids.values()).isdisjoint(set(old_ids.values()))

            # IDs are strictly increasing in (date_created, old-id) order:
            # B and D share a millisecond, broken by the smaller old id (B).
            assert new_ids["B"] < new_ids["D"] < new_ids["C"] < new_ids["A"]

            # IDs are time-ordered: the high bits encode ms since the epoch.
            assert new_ids["B"] >> RESOURCE_ID_RANDOM_BITS == 1000
            assert new_ids["C"] >> RESOURCE_ID_RANDOM_BITS == 2000
            assert new_ids["A"] >> RESOURCE_ID_RANDOM_BITS == 3000

            # Dependent foreign keys follow the re-mint with no dangling rows.
            doc_ids = {
                handle: resource_id
                for resource_id, handle in (
                    await conn.execute(
                        sa.text("SELECT id, handle FROM document_resource")
                    )
                ).all()
            }
            assert doc_ids["SQR-001"] == new_ids["A"]
            assert doc_ids["DMTN-002"] == new_ids["C"]

            contrib = {
                role_resource[0]: role_resource[1]
                for role_resource in (
                    await conn.execute(
                        sa.text("SELECT id, resource_id FROM contributor")
                    )
                ).all()
            }
            assert contrib[1] == new_ids["A"]
            assert contrib[2] == new_ids["B"]

            rel = {
                row[0]: (row[1], row[2], row[3])
                for row in (
                    await conn.execute(
                        sa.text(
                            "SELECT id, source_resource_id, "
                            "related_resource_id, related_external_ref_id "
                            "FROM resource_relation"
                        )
                    )
                ).all()
            }
            assert rel[1] == (new_ids["A"], new_ids["C"], None)
            assert rel[2] == (new_ids["B"], None, 9001)

            # No orphaned references anywhere.
            for table, column in (
                ("document_resource", "id"),
                ("contributor", "resource_id"),
                ("resource_relation", "source_resource_id"),
                ("resource_relation", "related_resource_id"),
            ):
                orphans = (
                    await conn.execute(
                        sa.text(
                            f"SELECT count(*) FROM {table} t "  # noqa: S608
                            f"LEFT JOIN resource r ON t.{column} = r.id "
                            f"WHERE t.{column} IS NOT NULL AND r.id IS NULL"
                        )
                    )
                ).scalar_one()
                assert orphans == 0

            # The dropped foreign keys were recreated.
            fk_names = {
                row[0]
                for row in (
                    await conn.execute(
                        sa.text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE contype = 'f'"
                        )
                    )
                ).all()
            }
            assert {
                "contributor_resource_id_fkey",
                "document_resource_id_fkey",
                "resource_relation_source_resource_id_fkey",
                "resource_relation_related_resource_id_fkey",
            } <= fk_names
    finally:
        await engine.dispose()
