"""Change table names from plural to singular

Revision ID: fb5ed49d63d5
Revises: 7ea34679824b
Create Date: 2025-06-20 18:24:49.260276+00:00
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fb5ed49d63d5"
down_revision: str | None = "7ea34679824b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename tables from plural to singular."""
    # Rename main tables
    op.rename_table("authors", "author")
    op.rename_table("collaborations", "collaboration")
    op.rename_table("affiliations", "affiliation")
    op.rename_table("terms", "term")
    op.rename_table("links", "link")
    op.rename_table("sdm_schemas", "sdm_schema")
    op.rename_table("sdm_tables", "sdm_table")
    op.rename_table("sdm_columns", "sdm_column")

    # Rename indexes to match the new table names
    # Author table indexes
    op.drop_index("ix_authors_given_name", table_name="author")
    op.drop_index("ix_authors_internal_id", table_name="author")
    op.drop_index("ix_authors_surname", table_name="author")
    op.create_index("ix_author_given_name", "author", ["given_name"])
    op.create_index(
        "ix_author_internal_id", "author", ["internal_id"], unique=True
    )
    op.create_index("ix_author_surname", "author", ["surname"])

    # Collaboration table indexes
    op.drop_index("ix_collaborations_internal_id", table_name="collaboration")
    op.drop_index("ix_collaborations_name", table_name="collaboration")
    op.create_index(
        "ix_collaboration_internal_id",
        "collaboration",
        ["internal_id"],
        unique=True,
    )
    op.create_index("ix_collaboration_name", "collaboration", ["name"])

    # Affiliation table indexes
    op.drop_index("ix_affiliations_address", table_name="affiliation")
    op.drop_index("ix_affiliations_internal_id", table_name="affiliation")
    op.drop_index("ix_affiliations_name", table_name="affiliation")
    op.create_index("ix_affiliation_address", "affiliation", ["address"])
    op.create_index(
        "ix_affiliation_internal_id",
        "affiliation",
        ["internal_id"],
        unique=True,
    )
    op.create_index("ix_affiliation_name", "affiliation", ["name"])

    # Term table indexes
    op.drop_index("ix_terms_contexts", table_name="term")
    op.drop_index("ix_terms_definition", table_name="term")
    op.drop_index("ix_terms_term", table_name="term")
    op.create_index("ix_term_contexts", "term", ["contexts"])
    op.create_index("ix_term_definition", "term", ["definition"])
    op.create_index("ix_term_term", "term", ["term"])

    # SDM Schema table indexes
    op.drop_index("ix_sdm_schemas_name", table_name="sdm_schema")
    op.create_index("ix_sdm_schema_name", "sdm_schema", ["name"])

    # SDM Table table indexes
    op.drop_index("ix_sdm_tables_name", table_name="sdm_table")
    op.drop_index("ix_sdm_tables_schema_id", table_name="sdm_table")
    op.create_index("ix_sdm_table_name", "sdm_table", ["name"])
    op.create_index("ix_sdm_table_schema_id", "sdm_table", ["schema_id"])

    # SDM Column table indexes
    op.drop_index("ix_sdm_columns_name", table_name="sdm_column")
    op.drop_index("ix_sdm_columns_table_id", table_name="sdm_column")
    op.create_index("ix_sdm_column_name", "sdm_column", ["name"])
    op.create_index("ix_sdm_column_table_id", "sdm_column", ["table_id"])


def downgrade() -> None:
    """Rename tables back from singular to plural."""
    # Reverse the index renames first
    # SDM Column table indexes
    op.drop_index("ix_sdm_column_table_id", table_name="sdm_column")
    op.drop_index("ix_sdm_column_name", table_name="sdm_column")
    op.create_index("ix_sdm_columns_table_id", "sdm_column", ["table_id"])
    op.create_index("ix_sdm_columns_name", "sdm_column", ["name"])

    # SDM Table table indexes
    op.drop_index("ix_sdm_table_schema_id", table_name="sdm_table")
    op.drop_index("ix_sdm_table_name", table_name="sdm_table")
    op.create_index("ix_sdm_tables_schema_id", "sdm_table", ["schema_id"])
    op.create_index("ix_sdm_tables_name", "sdm_table", ["name"])

    # SDM Schema table indexes
    op.drop_index("ix_sdm_schema_name", table_name="sdm_schema")
    op.create_index("ix_sdm_schemas_name", "sdm_schema", ["name"])

    # Term table indexes
    op.drop_index("ix_term_term", table_name="term")
    op.drop_index("ix_term_definition", table_name="term")
    op.drop_index("ix_term_contexts", table_name="term")
    op.create_index("ix_terms_term", "term", ["term"])
    op.create_index("ix_terms_definition", "term", ["definition"])
    op.create_index("ix_terms_contexts", "term", ["contexts"])

    # Affiliation table indexes
    op.drop_index("ix_affiliation_name", table_name="affiliation")
    op.drop_index("ix_affiliation_internal_id", table_name="affiliation")
    op.drop_index("ix_affiliation_address", table_name="affiliation")
    op.create_index("ix_affiliations_name", "affiliation", ["name"])
    op.create_index(
        "ix_affiliations_internal_id",
        "affiliation",
        ["internal_id"],
        unique=True,
    )
    op.create_index("ix_affiliations_address", "affiliation", ["address"])

    # Collaboration table indexes
    op.drop_index("ix_collaboration_name", table_name="collaboration")
    op.drop_index("ix_collaboration_internal_id", table_name="collaboration")
    op.create_index("ix_collaborations_name", "collaboration", ["name"])
    op.create_index(
        "ix_collaborations_internal_id",
        "collaboration",
        ["internal_id"],
        unique=True,
    )

    # Author table indexes
    op.drop_index("ix_author_surname", table_name="author")
    op.drop_index("ix_author_internal_id", table_name="author")
    op.drop_index("ix_author_given_name", table_name="author")
    op.create_index("ix_authors_surname", "author", ["surname"])
    op.create_index(
        "ix_authors_internal_id", "author", ["internal_id"], unique=True
    )
    op.create_index("ix_authors_given_name", "author", ["given_name"])

    # Reverse the table renames
    op.rename_table("author", "authors")
    op.rename_table("collaboration", "collaborations")
    op.rename_table("affiliation", "affiliations")
    op.rename_table("term", "terms")
    op.rename_table("link", "links")
    op.rename_table("sdm_schema", "sdm_schemas")
    op.rename_table("sdm_table", "sdm_tables")
    op.rename_table("sdm_column", "sdm_columns")
