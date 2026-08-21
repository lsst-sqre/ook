"""Rename linkcheck datetime columns to the date_ prefix

Revision ID: f43554a10acb
Revises: 0bfe17a5f990
Create Date: 2026-08-21 15:03:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f43554a10acb"
down_revision: str | None = "0bfe17a5f990"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, old column name, new column name). Ook's API convention is that
# every datetime-valued field carries a ``date_`` prefix; linkcheck was the
# sole outlier.
_COLUMN_RENAMES = (
    ("checked_url", "last_checked_at", "date_last_checked"),
    ("checked_url", "last_ok_at", "date_last_ok"),
    ("checked_url", "failing_since", "date_failing_since"),
    ("checked_url", "next_check_at", "date_next_check"),
    ("linkcheck_contribution", "checked_at", "date_checked"),
)

# Postgres keeps an index's name when its column is renamed, but SQLAlchemy
# derives these names from the column, so they have to be renamed too or the
# live schema drifts from the ORM metadata.
_INDEX_RENAMES = (
    ("ix_checked_url_last_checked_at", "ix_checked_url_date_last_checked"),
    ("ix_checked_url_next_check_at", "ix_checked_url_date_next_check"),
)


def upgrade() -> None:
    # Rename rather than drop/add so existing rows keep their timestamps.
    for table, old_name, new_name in _COLUMN_RENAMES:
        op.alter_column(table, old_name, new_column_name=new_name)
    for old_name, new_name in _INDEX_RENAMES:
        op.execute(f"ALTER INDEX {old_name} RENAME TO {new_name}")


def downgrade() -> None:
    for old_name, new_name in _INDEX_RENAMES:
        op.execute(f"ALTER INDEX {new_name} RENAME TO {old_name}")
    for table, old_name, new_name in _COLUMN_RENAMES:
        op.alter_column(table, new_name, new_column_name=old_name)
