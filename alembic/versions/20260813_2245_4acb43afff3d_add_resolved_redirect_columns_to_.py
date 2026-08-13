"""Add resolved redirect columns to intersphinx_inventory

Revision ID: 4acb43afff3d
Revises: c2a2c14c0e60
Create Date: 2026-08-13 22:45:00.271202+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4acb43afff3d"
down_revision: str | None = "c2a2c14c0e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Both columns are nullable with no backfill: null is the meaningful
    # "the chain did not redirect" value, so rows cached before this
    # migration are indistinguishable from non-redirecting ones until their
    # next fetch or refresh populates them.
    op.add_column(
        "intersphinx_inventory",
        sa.Column("resolved_url", sa.UnicodeText(), nullable=True),
    )
    op.add_column(
        "intersphinx_inventory",
        sa.Column("resolved_redirect_permanent", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("intersphinx_inventory", "resolved_redirect_permanent")
    op.drop_column("intersphinx_inventory", "resolved_url")
