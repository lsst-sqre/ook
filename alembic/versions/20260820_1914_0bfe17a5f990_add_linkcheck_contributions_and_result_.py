"""Add linkcheck contributions and result source

Revision ID: 0bfe17a5f990
Revises: 6f68334c9c9b
Create Date: 2026-08-20 19:14:43.491407+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0bfe17a5f990"
down_revision: str | None = "6f68334c9c9b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "linkcheck_contribution",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("check_id", sa.BigInteger(), nullable=False),
        sa.Column("checked_url_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.UnicodeText(), nullable=False),
        sa.Column("repository", sa.UnicodeText(), nullable=False),
        sa.Column("run_id", sa.UnicodeText(), nullable=False),
        sa.Column("workflow_ref", sa.UnicodeText(), nullable=False),
        sa.Column("run_url", sa.UnicodeText(), nullable=True),
        sa.Column("checker_version", sa.UnicodeText(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("redirect_url", sa.UnicodeText(), nullable=True),
        sa.Column("redirect_status_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.UnicodeText(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("date_received", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["check_id"], ["linkcheck_check.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["checked_url_id"], ["checked_url.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_linkcheck_contribution_check_id"),
        "linkcheck_contribution",
        ["check_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_linkcheck_contribution_checked_url_id"),
        "linkcheck_contribution",
        ["checked_url_id"],
        unique=False,
    )
    # Add the column NOT NULL with a temporary server default so existing
    # rows backfill to ``server`` — every result recorded before this
    # migration came from Ook's own checking — then drop the default so
    # the column matches the ORM model, which supplies the value on every
    # insert (like check_method).
    op.add_column(
        "checked_url",
        sa.Column(
            "result_source",
            sa.UnicodeText(),
            nullable=False,
            server_default="server",
        ),
    )
    op.alter_column("checked_url", "result_source", server_default=None)
    op.add_column(
        "checked_url",
        sa.Column("contributed_by", sa.UnicodeText(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("checked_url", "contributed_by")
    op.drop_column("checked_url", "result_source")
    op.drop_index(
        op.f("ix_linkcheck_contribution_checked_url_id"),
        table_name="linkcheck_contribution",
    )
    op.drop_index(
        op.f("ix_linkcheck_contribution_check_id"),
        table_name="linkcheck_contribution",
    )
    op.drop_table("linkcheck_contribution")
