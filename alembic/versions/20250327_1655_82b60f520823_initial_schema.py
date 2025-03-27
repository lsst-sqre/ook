"""Initial schema.

Revision ID: 82b60f520823
Revises:
Create Date: 2025-03-27 16:55:02.550702+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "82b60f520823"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "links",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("html_url", sa.UnicodeText(), nullable=False),
        sa.Column("source_type", sa.UnicodeText(), nullable=False),
        sa.Column("source_title", sa.UnicodeText(), nullable=False),
        sa.Column("source_collection_title", sa.UnicodeText(), nullable=True),
        sa.Column("date_updated", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sdm_schemas",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.UnicodeText(), nullable=False),
        sa.Column("felis_id", sa.UnicodeText(), nullable=False),
        sa.Column("description", sa.UnicodeText(), nullable=True),
        sa.Column("github_owner", sa.UnicodeText(), nullable=False),
        sa.Column("github_repo", sa.UnicodeText(), nullable=False),
        sa.Column("github_ref", sa.UnicodeText(), nullable=False),
        sa.Column("github_path", sa.UnicodeText(), nullable=False),
        sa.Column("date_updated", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_sdm_schema_name"),
    )
    op.create_index(
        op.f("ix_sdm_schemas_name"), "sdm_schemas", ["name"], unique=False
    )
    op.create_table(
        "links_sdm_schemas",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("schema_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["id"],
            ["links.id"],
        ),
        sa.ForeignKeyConstraint(
            ["schema_id"],
            ["sdm_schemas.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_links_sdm_schemas_schema_id"),
        "links_sdm_schemas",
        ["schema_id"],
        unique=False,
    )
    op.create_table(
        "sdm_tables",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("schema_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.UnicodeText(), nullable=False),
        sa.Column("felis_id", sa.UnicodeText(), nullable=False),
        sa.Column("description", sa.UnicodeText(), nullable=True),
        sa.Column("tap_table_index", sa.BigInteger(), nullable=True),
        sa.Column("date_updated", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["schema_id"],
            ["sdm_schemas.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "schema_id", "name", name="uq_sdm_table_schema_name"
        ),
    )
    op.create_index(
        op.f("ix_sdm_tables_name"), "sdm_tables", ["name"], unique=False
    )
    op.create_index(
        op.f("ix_sdm_tables_schema_id"),
        "sdm_tables",
        ["schema_id"],
        unique=False,
    )
    op.create_table(
        "links_sdm_tables",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("table_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["id"],
            ["links.id"],
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["sdm_tables.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_links_sdm_tables_table_id"),
        "links_sdm_tables",
        ["table_id"],
        unique=False,
    )
    op.create_table(
        "sdm_columns",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("table_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.UnicodeText(), nullable=False),
        sa.Column("felis_id", sa.UnicodeText(), nullable=False),
        sa.Column("description", sa.UnicodeText(), nullable=True),
        sa.Column("datatype", sa.UnicodeText(), nullable=False),
        sa.Column("ivoa_ucd", sa.UnicodeText(), nullable=True),
        sa.Column("ivoa_unit", sa.UnicodeText(), nullable=True),
        sa.Column("tap_column_index", sa.BigInteger(), nullable=True),
        sa.Column("date_updated", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["sdm_tables.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "table_id", "name", name="uq_sdm_column_table_name"
        ),
    )
    op.create_index(
        op.f("ix_sdm_columns_name"), "sdm_columns", ["name"], unique=False
    )
    op.create_index(
        op.f("ix_sdm_columns_table_id"),
        "sdm_columns",
        ["table_id"],
        unique=False,
    )
    op.create_table(
        "links_sdm_columns",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("column_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["column_id"],
            ["sdm_columns.id"],
        ),
        sa.ForeignKeyConstraint(
            ["id"],
            ["links.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_links_sdm_columns_column_id"),
        "links_sdm_columns",
        ["column_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_links_sdm_columns_column_id"), table_name="links_sdm_columns"
    )
    op.drop_table("links_sdm_columns")
    op.drop_index(op.f("ix_sdm_columns_table_id"), table_name="sdm_columns")
    op.drop_index(op.f("ix_sdm_columns_name"), table_name="sdm_columns")
    op.drop_table("sdm_columns")
    op.drop_index(
        op.f("ix_links_sdm_tables_table_id"), table_name="links_sdm_tables"
    )
    op.drop_table("links_sdm_tables")
    op.drop_index(op.f("ix_sdm_tables_schema_id"), table_name="sdm_tables")
    op.drop_index(op.f("ix_sdm_tables_name"), table_name="sdm_tables")
    op.drop_table("sdm_tables")
    op.drop_index(
        op.f("ix_links_sdm_schemas_schema_id"), table_name="links_sdm_schemas"
    )
    op.drop_table("links_sdm_schemas")
    op.drop_index(op.f("ix_sdm_schemas_name"), table_name="sdm_schemas")
    op.drop_table("sdm_schemas")
    op.drop_table("links")
