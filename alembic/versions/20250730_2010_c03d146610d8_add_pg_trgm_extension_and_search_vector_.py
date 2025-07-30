"""Add pg_trgm extension and search_vector for fuzzy author search

Revision ID: c03d146610d8
Revises: 1ad667eab84e
Create Date: 2025-07-30 20:10:18.783706+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c03d146610d8"
down_revision: str | None = "1ad667eab84e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Enable pg_trgm extension for trigram similarity matching
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Add search_vector generated column to author table
    op.add_column(
        "author",
        sa.Column(
            "search_vector",
            sa.UnicodeText,
            sa.Computed(
                "COALESCE(given_name || ' ', '') || surname || ' ' || "
                "COALESCE(surname || ', ' || given_name, '')",
                persisted=True,
            ),
            nullable=False,
        ),
    )

    # Create trigram indexes for fuzzy search
    op.create_index(
        "idx_author_surname_trgm",
        "author",
        ["surname"],
        postgresql_using="gin",
        postgresql_ops={"surname": "gin_trgm_ops"},
    )

    op.create_index(
        "idx_author_given_name_trgm",
        "author",
        ["given_name"],
        postgresql_using="gin",
        postgresql_ops={"given_name": "gin_trgm_ops"},
    )

    op.create_index(
        "idx_author_search_vector_trgm",
        "author",
        ["search_vector"],
        postgresql_using="gin",
        postgresql_ops={"search_vector": "gin_trgm_ops"},
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("idx_author_search_vector_trgm", table_name="author")
    op.drop_index("idx_author_given_name_trgm", table_name="author")
    op.drop_index("idx_author_surname_trgm", table_name="author")

    # Drop search_vector column
    op.drop_column("author", "search_vector")

    # Note: We don't drop the pg_trgm extension as it might be used elsewhere
