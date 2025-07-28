"""Add resource tables

Revision ID: 1ad667eab84e
Revises: 113ced7d2d29
Create Date: 2025-07-18 20:48:07.574879+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1ad667eab84e"
down_revision: str | None = "113ced7d2d29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_reference",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("url", sa.UnicodeText(), nullable=True),
        sa.Column("doi", sa.UnicodeText(), nullable=True),
        sa.Column("arxiv_id", sa.UnicodeText(), nullable=True),
        sa.Column("isbn", sa.UnicodeText(), nullable=True),
        sa.Column("issn", sa.UnicodeText(), nullable=True),
        sa.Column("ads_bibcode", sa.UnicodeText(), nullable=True),
        sa.Column("type", sa.UnicodeText(), nullable=True),
        sa.Column("title", sa.UnicodeText(), nullable=True),
        sa.Column("publication_year", sa.UnicodeText(), nullable=True),
        sa.Column("volume", sa.UnicodeText(), nullable=True),
        sa.Column("issue", sa.UnicodeText(), nullable=True),
        sa.Column("number", sa.UnicodeText(), nullable=True),
        sa.Column("number_type", sa.UnicodeText(), nullable=True),
        sa.Column("first_page", sa.UnicodeText(), nullable=True),
        sa.Column("last_page", sa.UnicodeText(), nullable=True),
        sa.Column("publisher", sa.UnicodeText(), nullable=True),
        sa.Column("edition", sa.UnicodeText(), nullable=True),
        sa.Column("contributors", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("doi"),
        sa.UniqueConstraint("arxiv_id"),
        sa.UniqueConstraint("isbn"),
        sa.UniqueConstraint("issn"),
        sa.UniqueConstraint("ads_bibcode"),
    )
    op.create_table(
        "resource",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("resource_class", sa.String(), nullable=True),
        sa.Column("date_created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("date_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.UnicodeText(), nullable=False),
        sa.Column("description", sa.UnicodeText(), nullable=True),
        sa.Column("url", sa.UnicodeText(), nullable=True),
        sa.Column("doi", sa.UnicodeText(), nullable=True),
        sa.Column(
            "date_resource_published",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "date_resource_updated", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("version", sa.String(), nullable=True),
        sa.Column("type", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("doi"),
    )
    op.create_table(
        "contributor",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("role", sa.UnicodeText(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["author.id"],
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["resource.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_id",
            "order",
            "role",
            name="uq_contributor_resource_order_role",
        ),
    )
    op.create_table(
        "document_resource",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("series", sa.UnicodeText(), nullable=False),
        sa.Column("handle", sa.UnicodeText(), nullable=False),
        sa.Column("generator", sa.UnicodeText(), nullable=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["id"],
            ["resource.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("handle"),
        sa.UniqueConstraint(
            "series", "number", name="uq_document_series_number"
        ),
    )
    op.create_table(
        "resource_relation",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_resource_id", sa.BigInteger(), nullable=False),
        sa.Column("related_resource_id", sa.BigInteger(), nullable=True),
        sa.Column("related_external_ref_id", sa.BigInteger(), nullable=True),
        sa.Column("relation_type", sa.UnicodeText(), nullable=False),
        sa.ForeignKeyConstraint(
            ["related_external_ref_id"],
            ["external_reference.id"],
        ),
        sa.ForeignKeyConstraint(
            ["related_resource_id"],
            ["resource.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_resource_id"],
            ["resource.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_resource_id",
            "related_resource_id",
            "related_external_ref_id",
            "relation_type",
            name="uq_resource_relation",
        ),
    )

    # Add indexes for query optimization
    op.create_index(
        "idx_resource_class", "resource", ["resource_class"], unique=False
    )
    op.create_index(
        "idx_resource_date_published",
        "resource",
        ["date_resource_published"],
        unique=False,
    )
    op.create_index(
        "idx_resource_date_updated",
        "resource",
        ["date_resource_updated"],
        unique=False,
    )
    op.create_index(
        "idx_resource_relation_source",
        "resource_relation",
        ["source_resource_id"],
        unique=False,
    )
    op.create_index(
        "idx_resource_relation_type",
        "resource_relation",
        ["relation_type"],
        unique=False,
    )
    op.create_index(
        "idx_document_series_number",
        "document_resource",
        ["series", "number"],
        unique=False,
    )


def downgrade() -> None:
    # Drop indexes first
    op.drop_index("idx_document_series_number", table_name="document_resource")
    op.drop_index("idx_resource_relation_type", table_name="resource_relation")
    op.drop_index(
        "idx_resource_relation_source", table_name="resource_relation"
    )
    op.drop_index("idx_resource_date_updated", table_name="resource")
    op.drop_index("idx_resource_date_published", table_name="resource")
    op.drop_index("idx_resource_class", table_name="resource")

    # Drop tables
    op.drop_table("resource_relation")
    op.drop_table("document_resource")
    op.drop_table("contributor")
    op.drop_table("resource")
    op.drop_table("external_reference")
