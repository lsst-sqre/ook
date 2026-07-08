"""Drop linkcheck_check id autoincrement

Revision ID: a62deab01a9b
Revises: e3224f7fa2cb
Create Date: 2026-07-08 15:29:52.734130+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a62deab01a9b"
down_revision: str | None = "e3224f7fa2cb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # linkcheck_check.id is now minted as a Crockford Base32 integer by
    # the application, so drop the auto-increment sequence default and
    # the owned sequence itself. Autogenerate treats the SERIAL sequence
    # as implicit and does not emit these, so they are hand-written.
    op.alter_column("linkcheck_check", "id", server_default=None)
    op.execute("DROP SEQUENCE IF EXISTS linkcheck_check_id_seq")


def downgrade() -> None:
    # Restore the SERIAL auto-increment behavior.
    op.execute(
        "CREATE SEQUENCE linkcheck_check_id_seq OWNED BY linkcheck_check.id"
    )
    op.alter_column(
        "linkcheck_check",
        "id",
        server_default=sa.text("nextval('linkcheck_check_id_seq'::regclass)"),
    )
