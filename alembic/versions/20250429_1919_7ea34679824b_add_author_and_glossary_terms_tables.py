"""Add author and glossary terms tables

Revision ID: 7ea34679824b
Revises: 82b60f520823
Create Date: 2025-04-29 19:19:47.454132+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7ea34679824b"
down_revision: str | None = "82b60f520823"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create pg_trgm extension for text similarity search
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    op.create_table(
        "affiliations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("internal_id", sa.UnicodeText(), nullable=False),
        sa.Column("name", sa.UnicodeText(), nullable=False),
        sa.Column("address", sa.UnicodeText(), nullable=True),
        sa.Column("date_updated", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "internal_id", "name", name="uq_affiliation_internal_id_name"
        ),
    )
    op.create_index(
        op.f("ix_affiliations_address"),
        "affiliations",
        ["address"],
        unique=False,
    )
    op.create_index(
        op.f("ix_affiliations_internal_id"),
        "affiliations",
        ["internal_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_affiliations_name"), "affiliations", ["name"], unique=False
    )
    op.create_table(
        "authors",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("internal_id", sa.UnicodeText(), nullable=False),
        sa.Column("surname", sa.UnicodeText(), nullable=False),
        sa.Column("given_name", sa.UnicodeText(), nullable=True),
        sa.Column("notes", postgresql.ARRAY(sa.UnicodeText()), nullable=False),
        sa.Column("email", sa.UnicodeText(), nullable=True),
        sa.Column("orcid", sa.UnicodeText(), nullable=True),
        sa.Column("date_updated", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("orcid"),
    )
    op.create_index(
        op.f("ix_authors_given_name"), "authors", ["given_name"], unique=False
    )
    op.create_index(
        op.f("ix_authors_internal_id"), "authors", ["internal_id"], unique=True
    )
    op.create_index(
        op.f("ix_authors_surname"), "authors", ["surname"], unique=False
    )
    op.create_table(
        "collaborations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("internal_id", sa.UnicodeText(), nullable=False),
        sa.Column("name", sa.UnicodeText(), nullable=False),
        sa.Column("date_updated", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "internal_id", "name", name="uq_collaboration_internal_id_name"
        ),
    )
    op.create_index(
        op.f("ix_collaborations_internal_id"),
        "collaborations",
        ["internal_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_collaborations_name"),
        "collaborations",
        ["name"],
        unique=False,
    )
    op.create_table(
        "terms",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("term", sa.UnicodeText(), nullable=False),
        sa.Column("definition", sa.UnicodeText(), nullable=False),
        sa.Column("definition_es", sa.UnicodeText(), nullable=True),
        sa.Column("is_abbr", sa.Boolean(), nullable=False),
        sa.Column(
            "contexts", postgresql.ARRAY(sa.UnicodeText()), nullable=False
        ),
        sa.Column(
            "related_documentation",
            postgresql.ARRAY(sa.UnicodeText()),
            nullable=False,
        ),
        sa.Column("date_updated", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("term", "definition", name="uq_term_definition"),
    )
    op.create_index(
        op.f("ix_terms_contexts"), "terms", ["contexts"], unique=False
    )
    op.create_index(
        op.f("ix_terms_definition"), "terms", ["definition"], unique=False
    )
    op.create_index(op.f("ix_terms_term"), "terms", ["term"], unique=False)
    op.create_table(
        "author_affiliations",
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("affiliation_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["affiliation_id"],
            ["affiliations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["authors.id"],
        ),
        sa.PrimaryKeyConstraint("author_id", "affiliation_id"),
    )
    op.create_table(
        "term_relationships",
        sa.Column("source_term_id", sa.Integer(), nullable=False),
        sa.Column("related_term_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["related_term_id"], ["terms.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_term_id"], ["terms.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("source_term_id", "related_term_id"),
    )


def downgrade() -> None:
    op.drop_table("term_relationships")
    op.drop_table("author_affiliations")
    op.drop_index(op.f("ix_terms_term"), table_name="terms")
    op.drop_index(op.f("ix_terms_definition"), table_name="terms")
    op.drop_index(op.f("ix_terms_contexts"), table_name="terms")
    op.drop_table("terms")
    op.drop_index(op.f("ix_collaborations_name"), table_name="collaborations")
    op.drop_index(
        op.f("ix_collaborations_internal_id"), table_name="collaborations"
    )
    op.drop_table("collaborations")
    op.drop_index(op.f("ix_authors_surname"), table_name="authors")
    op.drop_index(op.f("ix_authors_internal_id"), table_name="authors")
    op.drop_index(op.f("ix_authors_given_name"), table_name="authors")
    op.drop_table("authors")
    op.drop_index(op.f("ix_affiliations_name"), table_name="affiliations")
    op.drop_index(
        op.f("ix_affiliations_internal_id"), table_name="affiliations"
    )
    op.drop_index(op.f("ix_affiliations_address"), table_name="affiliations")
    op.drop_table("affiliations")
