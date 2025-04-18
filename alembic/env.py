"""Alembic migration environment."""

from safir.database import run_migrations_offline, run_migrations_online
from safir.logging import configure_alembic_logging, configure_logging

from alembic import context
from ook.config import config
from ook.dbschema import Base

# Configure structlog.
configure_logging(name="ook", log_level=config.log_level)
configure_alembic_logging()

# Run the migrations.
if context.is_offline_mode():
    run_migrations_offline(Base.metadata, config.database_url)
else:
    run_migrations_online(
        Base.metadata,
        config.database_url,
        config.database_password,
    )
