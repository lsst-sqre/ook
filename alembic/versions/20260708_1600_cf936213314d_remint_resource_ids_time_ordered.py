"""Re-mint resource IDs in date_created order

Revision ID: cf936213314d
Revises: a62deab01a9b
Create Date: 2026-07-08 16:00:00.000000+00:00

This is a one-time, deliberate resource-ID break (PRD ook#238, decision D1). It
re-mints every ``resource.id`` as a time-ordered Crockford Base32 identifier
derived from the row's ``date_created`` (via
:func:`ook.domain.base32id.mint_time_ordered_resource_ids`) so IDs sort in
creation order and stay strictly increasing even when rows share a millisecond.
Dependent foreign keys in ``document_resource``, ``contributor`` and
``resource_relation`` are updated in the same transaction so no reference is
left dangling. No external consumers hold resource IDs yet.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op
from ook.domain.base32id import mint_time_ordered_resource_ids

# revision identifiers, used by Alembic.
revision: str = "cf936213314d"
down_revision: str | None = "a62deab01a9b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Foreign keys that reference ``resource.id`` and must be dropped before the
# primary keys are re-minted, then recreated identically afterwards. Order is
# irrelevant on drop; recreation matches the schema so ``alembic check`` stays
# clean.
_RESOURCE_FKS: tuple[tuple[str, str, str, str], ...] = (
    ("contributor", "contributor_resource_id_fkey", "resource_id", "resource"),
    ("document_resource", "document_resource_id_fkey", "id", "resource"),
    (
        "resource_relation",
        "resource_relation_source_resource_id_fkey",
        "source_resource_id",
        "resource",
    ),
    (
        "resource_relation",
        "resource_relation_related_resource_id_fkey",
        "related_resource_id",
        "resource",
    ),
)

# Columns that hold a ``resource.id`` value and must be remapped in lockstep.
_RESOURCE_ID_COLUMNS: tuple[tuple[str, str], ...] = (
    ("resource", "id"),
    ("document_resource", "id"),
    ("contributor", "resource_id"),
    ("resource_relation", "source_resource_id"),
    ("resource_relation", "related_resource_id"),
)

# A disjoint temporary keyspace for the two-pass remap. Minted IDs occupy the
# low 60 bits, so shifting through ``id + 2**62`` guarantees no intermediate
# value ever collides with an as-yet-unshifted (or already-final) ID, avoiding
# spurious unique-index violations mid-UPDATE. ``2**62 + 2**60`` still fits a
# signed 64-bit ``BIGINT``.
_REMAP_OFFSET = 1 << 62


def remint_resource_ids(connection: Connection) -> None:
    """Re-mint every ``resource.id`` in ``date_created`` order and rewrite all
    dependent foreign keys within the current transaction.

    Parameters
    ----------
    connection
        A synchronous SQLAlchemy connection bound to the migration transaction
        (``op.get_bind()``). Exposed as a standalone function so the remap can
        be exercised directly against a seeded database in tests.
    """
    rows = connection.execute(
        sa.text(
            "SELECT id, date_created FROM resource "
            "ORDER BY date_created ASC, id ASC"
        )
    ).all()
    if not rows:
        # Nothing to re-mint (e.g. a fresh database); leave the schema as is.
        return

    old_ids = [row[0] for row in rows]
    new_ids = mint_time_ordered_resource_ids([row[1] for row in rows])
    remap = [
        {"old_id": old_id, "tmp_id": new_id + _REMAP_OFFSET}
        for old_id, new_id in zip(old_ids, new_ids, strict=True)
    ]

    # Drop the resource-referencing foreign keys so the primary keys can move.
    for table, name, _column, _referent in _RESOURCE_FKS:
        connection.execute(
            sa.text(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
        )

    # Pass 1: shift every resource-ID-bearing column into the disjoint temp
    # keyspace (one UPDATE per row via executemany). Targets are all >= 2**62
    # and every current value < 2**60, so no shift collides with another row.
    for table, column in _RESOURCE_ID_COLUMNS:
        connection.execute(
            sa.text(
                f"UPDATE {table} SET {column} = :tmp_id "  # noqa: S608
                f"WHERE {column} = :old_id"
            ),
            remap,
        )

    # Pass 2: shift everything down to the final minted IDs in one statement
    # per column. Sources are all >= 2**62 and targets all < 2**60, so again
    # nothing collides. NULL references (external-only relations) are skipped.
    for table, column in _RESOURCE_ID_COLUMNS:
        connection.execute(
            sa.text(
                f"UPDATE {table} SET {column} = {column} - :offset "  # noqa: S608
                f"WHERE {column} >= :offset"
            ),
            {"offset": _REMAP_OFFSET},
        )

    # Recreate the foreign keys exactly as the schema declares them.
    for table, name, column, referent in _RESOURCE_FKS:
        connection.execute(
            sa.text(
                f"ALTER TABLE {table} ADD CONSTRAINT {name} "
                f"FOREIGN KEY ({column}) REFERENCES {referent}(id)"
            )
        )


def upgrade() -> None:
    remint_resource_ids(op.get_bind())


def downgrade() -> None:
    # Irreversible: the original random IDs are discarded by the re-mint and
    # cannot be reconstructed. The schema is unchanged, so this is a no-op.
    pass
