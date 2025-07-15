"""Drop collaboration table.

Revision ID: 113ced7d2d29
Revises: 176f421b2597
Create Date: 2025-07-15 17:30:15.521566+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "113ced7d2d29"
down_revision: str | None = "176f421b2597"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        op.f("ix_collaboration_internal_id"), table_name="collaboration"
    )
    op.drop_index(op.f("ix_collaboration_name"), table_name="collaboration")
    op.drop_table("collaboration")


def downgrade() -> None:
    op.create_table(
        "collaboration",
        sa.Column("id", sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column(
            "internal_id", sa.TEXT(), autoincrement=False, nullable=False
        ),
        sa.Column("name", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column(
            "date_updated",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("collaboration_pkey")),
        sa.UniqueConstraint(
            "internal_id",
            "name",
            name=op.f("uq_collaboration_internal_id_name"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_collaboration_name"), "collaboration", ["name"], unique=False
    )
    op.create_index(
        op.f("ix_collaboration_internal_id"),
        "collaboration",
        ["internal_id"],
        unique=True,
    )
