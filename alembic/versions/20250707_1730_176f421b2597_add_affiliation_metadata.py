"""Add affiliation metadata

Revision ID: 176f421b2597
Revises: fb5ed49d63d5
Create Date: 2025-07-07 17:30:56.922656+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "176f421b2597"
down_revision: str | None = "fb5ed49d63d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Rename unnamed ORCID constraint to have a proper name. This migration
    # accounts for the fact that the ORCID unique constraint was created
    # without a specific name, which can cause issues in some database setups.
    conn = op.get_bind()
    result = conn.execute(
        sa.text("""
        SELECT constraint_name
        FROM information_schema.table_constraints
        WHERE table_name = 'author'
        AND constraint_type = 'UNIQUE'
        AND constraint_name LIKE '%orcid%'
    """)
    )
    constraint_row = result.fetchone()

    if constraint_row:
        constraint_name = constraint_row[0]
        op.drop_constraint(constraint_name, "author", type_="unique")

    op.create_unique_constraint("uq_author_orcid", "author", ["orcid"])

    # Add affiliation metadata columns
    op.add_column(
        "affiliation", sa.Column("department", sa.UnicodeText(), nullable=True)
    )
    op.add_column(
        "affiliation",
        sa.Column("email_domain", sa.UnicodeText(), nullable=True),
    )
    op.add_column(
        "affiliation", sa.Column("ror_id", sa.UnicodeText(), nullable=True)
    )

    # Drop the old generic address column since we now have specific fields
    # First check if the index exists before trying to drop it
    conn = op.get_bind()
    index_result = conn.execute(
        sa.text("""
        SELECT indexname
        FROM pg_indexes
        WHERE tablename = 'affiliation'
        AND indexname LIKE '%address%'
    """)
    )
    index_row = index_result.fetchone()

    if index_row:
        index_name = index_row[0]
        op.drop_index(index_name, table_name="affiliation")

    op.drop_column("affiliation", "address")

    op.add_column(
        "affiliation",
        sa.Column("address_street", sa.UnicodeText(), nullable=True),
    )
    op.add_column(
        "affiliation",
        sa.Column("address_city", sa.UnicodeText(), nullable=True),
    )
    op.add_column(
        "affiliation",
        sa.Column("address_state", sa.UnicodeText(), nullable=True),
    )
    op.add_column(
        "affiliation",
        sa.Column("address_postal_code", sa.UnicodeText(), nullable=True),
    )
    op.add_column(
        "affiliation",
        sa.Column("address_country", sa.UnicodeText(), nullable=True),
    )


def downgrade() -> None:
    # Drop affiliation metadata
    op.drop_column("affiliation", "address_country")
    op.drop_column("affiliation", "address_postal_code")
    op.drop_column("affiliation", "address_state")
    op.drop_column("affiliation", "address_city")
    op.drop_column("affiliation", "address_street")

    # Restore the old generic address column
    op.add_column(
        "affiliation", sa.Column("address", sa.UnicodeText(), nullable=True)
    )
    # Create the index with the same name that would have been auto-generated
    # Check what the original index name was by looking at the pattern
    op.create_index(
        "ix_affiliation_address", "affiliation", ["address"], unique=False
    )

    op.drop_column("affiliation", "ror_id")
    op.drop_column("affiliation", "email_domain")
    op.drop_column("affiliation", "department")

    # Revert ORCID constraint naming
    op.drop_constraint("uq_author_orcid", "author", type_="unique")
    # Note: We create an unnamed constraint to match the original state
    # PostgreSQL will auto-generate a name like "author_orcid_key"
    op.create_unique_constraint(None, "author", ["orcid"])
