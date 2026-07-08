"""Rename linkcheck LTD columns to origin naming

The link-check service tracks any public website ("origin"), not just
LSST the Docs projects: ``ltd_slug`` becomes ``origin_base_url`` (a full
normalized URL), ``path`` becomes ``origin_path``, and
``default_branch`` becomes ``is_default_version``. Existing rows are
backfilled in place with the ``https://<slug>.lsst.io`` origin their
slug implied.

Revision ID: 97e2df2ad883
Revises: c1ddff6dff7a
Create Date: 2026-07-06 18:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "97e2df2ad883"
down_revision: str | None = "c1ddff6dff7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # linkcheck_check: ltd_slug -> origin_base_url (backfilled),
    # default_branch -> is_default_version.
    op.add_column(
        "linkcheck_check",
        sa.Column("origin_base_url", sa.UnicodeText(), nullable=True),
    )
    op.execute(
        "UPDATE linkcheck_check"
        " SET origin_base_url = 'https://' || ltd_slug || '.lsst.io'"
    )
    op.alter_column("linkcheck_check", "origin_base_url", nullable=False)
    op.create_index(
        op.f("ix_linkcheck_check_origin_base_url"),
        "linkcheck_check",
        ["origin_base_url"],
        unique=False,
    )
    op.drop_index(
        op.f("ix_linkcheck_check_ltd_slug"), table_name="linkcheck_check"
    )
    op.drop_column("linkcheck_check", "ltd_slug")
    op.alter_column(
        "linkcheck_check",
        "default_branch",
        new_column_name="is_default_version",
    )

    # url_occurrence: ltd_slug -> origin_base_url and path -> origin_path
    # (backfilled), with the unique constraint and index rebuilt on the
    # new columns.
    op.add_column(
        "url_occurrence",
        sa.Column("origin_base_url", sa.UnicodeText(), nullable=True),
    )
    op.add_column(
        "url_occurrence",
        sa.Column("origin_path", sa.UnicodeText(), nullable=True),
    )
    op.execute(
        "UPDATE url_occurrence"
        " SET origin_base_url = 'https://' || ltd_slug || '.lsst.io',"
        " origin_path = path"
    )
    op.alter_column("url_occurrence", "origin_base_url", nullable=False)
    op.alter_column("url_occurrence", "origin_path", nullable=False)
    op.drop_constraint("uq_url_occurrence", "url_occurrence", type_="unique")
    op.create_unique_constraint(
        "uq_url_occurrence",
        "url_occurrence",
        ["origin_base_url", "origin_path", "checked_url_id"],
    )
    op.create_index(
        op.f("ix_url_occurrence_origin_base_url"),
        "url_occurrence",
        ["origin_base_url"],
        unique=False,
    )
    op.drop_index(
        op.f("ix_url_occurrence_ltd_slug"), table_name="url_occurrence"
    )
    op.drop_column("url_occurrence", "ltd_slug")
    op.drop_column("url_occurrence", "path")


def downgrade() -> None:
    # url_occurrence: origin_base_url -> ltd_slug and origin_path ->
    # path, recovering the slug from the backfilled lsst.io origin.
    op.add_column(
        "url_occurrence",
        sa.Column("ltd_slug", sa.UnicodeText(), nullable=True),
    )
    op.add_column(
        "url_occurrence", sa.Column("path", sa.UnicodeText(), nullable=True)
    )
    op.execute(
        "UPDATE url_occurrence"
        r" SET ltd_slug = regexp_replace("
        r"origin_base_url, '^https://([^/.]+)\.lsst\.io$', '\1'),"
        " path = origin_path"
    )
    op.alter_column("url_occurrence", "ltd_slug", nullable=False)
    op.alter_column("url_occurrence", "path", nullable=False)
    op.drop_constraint("uq_url_occurrence", "url_occurrence", type_="unique")
    op.create_unique_constraint(
        "uq_url_occurrence",
        "url_occurrence",
        ["ltd_slug", "path", "checked_url_id"],
    )
    op.create_index(
        op.f("ix_url_occurrence_ltd_slug"),
        "url_occurrence",
        ["ltd_slug"],
        unique=False,
    )
    op.drop_index(
        op.f("ix_url_occurrence_origin_base_url"), table_name="url_occurrence"
    )
    op.drop_column("url_occurrence", "origin_base_url")
    op.drop_column("url_occurrence", "origin_path")

    # linkcheck_check: origin_base_url -> ltd_slug and
    # is_default_version -> default_branch.
    op.alter_column(
        "linkcheck_check",
        "is_default_version",
        new_column_name="default_branch",
    )
    op.add_column(
        "linkcheck_check",
        sa.Column("ltd_slug", sa.UnicodeText(), nullable=True),
    )
    op.execute(
        "UPDATE linkcheck_check"
        r" SET ltd_slug = regexp_replace("
        r"origin_base_url, '^https://([^/.]+)\.lsst\.io$', '\1')"
    )
    op.alter_column("linkcheck_check", "ltd_slug", nullable=False)
    op.create_index(
        op.f("ix_linkcheck_check_ltd_slug"),
        "linkcheck_check",
        ["ltd_slug"],
        unique=False,
    )
    op.drop_index(
        op.f("ix_linkcheck_check_origin_base_url"),
        table_name="linkcheck_check",
    )
    op.drop_column("linkcheck_check", "origin_base_url")
