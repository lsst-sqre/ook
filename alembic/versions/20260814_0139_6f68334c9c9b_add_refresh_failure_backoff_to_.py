"""Add refresh failure backoff to intersphinx_inventory

Revision ID: 6f68334c9c9b
Revises: 4acb43afff3d
Create Date: 2026-08-14 01:39:58.422768+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6f68334c9c9b"
down_revision: str | None = "4acb43afff3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable with no backfill: null is the meaningful "the last fetch
    # succeeded" value, so every existing row is simply eligible for its
    # next refresh, exactly as before this column existed.
    op.add_column(
        "intersphinx_inventory",
        sa.Column(
            "date_refresh_failed", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("intersphinx_inventory", "date_refresh_failed")
