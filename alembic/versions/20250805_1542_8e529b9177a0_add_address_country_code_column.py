"""Add address_country_code column

Revision ID: 8e529b9177a0
Revises: c03d146610d8
Create Date: 2025-08-05 15:42:15.097883+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8e529b9177a0"
down_revision: str | None = "c03d146610d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "affiliation",
        sa.Column("address_country_code", sa.String(length=2), nullable=True),
    )
    op.create_index(
        op.f("ix_affiliation_address_country_code"),
        "affiliation",
        ["address_country_code"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_affiliation_address_country_code"), table_name="affiliation"
    )
    op.drop_column("affiliation", "address_country_code")
