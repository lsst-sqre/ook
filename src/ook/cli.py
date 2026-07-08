"""Administrative command-line interface."""

from __future__ import annotations

import asyncio
import re
import subprocess
from dataclasses import dataclass
from datetime import timedelta
from itertools import batched
from pathlib import Path
from typing import Any

import click
import structlog
from algoliasearch.search_client import SearchClient
from safir.asyncio import run_with_asyncio
from safir.database import (
    create_database_engine,
    is_database_current,
    stamp_database,
)
from safir.logging import configure_logging

from ook.config import config
from ook.database import init_database
from ook.domain.algoliarecord import MinimalDocumentModel
from ook.domain.kafka import RecheckUrlsMessageV1
from ook.factory import Factory
from ook.services.algoliadocindex import AlgoliaDocIndexService

__all__ = [
    "LinkcheckRecheckSummary",
    "help",
    "main",
    "run_linkcheck_recheck",
    "upload_doc_stub",
]

# Add -h as a help shortcut option
CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(message="%(version)s")
def main() -> None:
    """Ook.

    Administrative command-line interface for ook.
    """
    configure_logging(
        profile=config.profile,
        log_level=config.log_level,
        name="ook",
    )


@main.command()
@click.argument("topic", default=None, required=False, nargs=1)
@click.pass_context
def help(ctx: click.Context, topic: None | str, **kw: Any) -> None:
    """Show help for any command."""
    # The help command implementation is taken from
    # https://www.burgundywall.com/post/having-click-help-subcommand
    if topic:
        if topic in main.commands:
            click.echo(main.commands[topic].get_help(ctx))
        else:
            raise click.UsageError(f"Unknown help topic {topic}", ctx)
    else:
        if not ctx.parent:
            raise RuntimeError("help called without topic or parent")
        click.echo(ctx.parent.get_help())


@main.command()
@click.option(
    "--alembic-config-path",
    envvar="OOK_ALEMBIC_CONFIG_PATH",
    type=click.Path(path_type=Path),
    help="Alembic configuration file.",
)
@click.option(
    "--reset", is_flag=True, help="Delete all existing database data."
)
def init(*, alembic_config_path: Path, reset: bool) -> None:
    """Initialize the SQL database storage."""
    logger = structlog.get_logger("ook")
    logger.debug("Initializing database")
    asyncio.run(init_database(config, logger, reset=reset))
    stamp_database(alembic_config_path)
    logger.debug("Finished initializing data stores")


@main.command()
@click.option(
    "--alembic-config-path",
    envvar="OOK_ALEMBIC_CONFIG_PATH",
    type=click.Path(path_type=Path),
    help="Alembic configuration file.",
)
def update_db_schema(*, alembic_config_path: Path) -> None:
    """Update the SQL database schema."""
    subprocess.run(
        ["alembic", "upgrade", "head"],
        check=True,
        cwd=str(alembic_config_path.parent),
    )


@main.command()
@click.option(
    "--alembic-config-path",
    envvar="OOK_ALEMBIC_CONFIG_PATH",
    type=click.Path(path_type=Path),
    help="Alembic configuration file.",
)
@run_with_asyncio
async def validate_db_schema(*, alembic_config_path: Path) -> None:
    """Validate that the SQL database schema is current."""
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    logger = structlog.get_logger("ook")
    if not await is_database_current(engine, logger, alembic_config_path):
        raise click.ClickException("Database schema is not current")


@main.command()
@click.option(
    "--dataset",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to the JSON-formatted document stub dataset to upload.",
)
@run_with_asyncio
async def upload_doc_stub(dataset: Path) -> None:
    """Upload a stub record for a document that can't be normally indexed.

    The schema for the document stub is the
    `ook.domain.algoliarecord.MinimalDocumentModel` Pydantic class.
    """
    logger = structlog.get_logger("ook")
    if any(
        _ is None
        for _ in (
            config.algolia_document_index_name,
            config.algolia_app_id,
            config.algolia_api_key,
        )
    ):
        raise click.UsageError("Algolia credentials not set in environment.")

    stub_record = MinimalDocumentModel.from_json(dataset.read_text())

    if config.algolia_api_key is None or config.algolia_app_id is None:
        raise RuntimeError(
            "Algolia app ID and API key must be set to use this service."
        )
    async with SearchClient.create(
        config.algolia_app_id,
        api_key=config.algolia_api_key.get_secret_value(),
    ) as client:
        index = client.init_index(config.algolia_document_index_name)
        algolia_doc_service = AlgoliaDocIndexService(index, logger)
        await algolia_doc_service.save_doc_stub(stub_record)


@main.command()
@click.option("--reingest", is_flag=True, help="Reingest missing documents.")
@run_with_asyncio
async def audit(*, reingest: bool = False) -> None:
    """Audit the Algolia document index and check if any documents are missing
    based on the listing of projects registered in the LTD Keeper service.
    """
    logger = structlog.get_logger("ook")
    if any(
        _ is None
        for _ in (
            config.algolia_document_index_name,
            config.algolia_app_id,
            config.algolia_api_key,
        )
    ):
        raise click.UsageError("Algolia credentials not set in environment.")
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    async with Factory.create_standalone(
        logger=logger, engine=engine
    ) as factory:
        algolia_audit_service = factory.create_algolia_audit_service()
        await algolia_audit_service.audit_missing_documents(
            ingest_missing=reingest
        )
    await engine.dispose()


@main.command(name="ingest-updated")
@click.option(
    "--window",
    default="2d",
    help="Time window to check for document updates. E.g. 2d, 1w, 1m, 1y.",
)
@run_with_asyncio
async def ingest_updated(*, window: str) -> None:
    """Ingest LTD projects updated recently."""
    logger = structlog.get_logger("ook")
    window_timedelta = parse_timedelta(window)
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    async with Factory.create_standalone(
        logger=logger, engine=engine
    ) as factory:
        classification_service = factory.create_classification_service()
        await classification_service.queue_ingest_for_updated_ltd_projects(
            window_timedelta
        )
    await engine.dispose()


@main.command(name="ingest-lsst-texmf")
@click.option(
    "--git-ref",
    default="main",
    help="Git ref (branch or tag) of the Git repository to use.",
)
@click.option(
    "--delete-stale-records",
    is_flag=True,
)
@run_with_asyncio
async def ingest_lsst_texmf(
    *, git_ref: str, delete_stale_records: bool
) -> None:
    """Update author and glossary data from GitHub."""
    logger = structlog.get_logger("ook")
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    async with Factory.create_standalone(
        logger=logger, engine=engine
    ) as factory:
        ingest_service = await factory.create_lsst_texmf_ingest_service()
        await ingest_service.ingest(
            git_ref=git_ref, delete_stale_records=delete_stale_records
        )
        await factory.db_session.commit()
    await engine.dispose()
    logger.info("Completed ingest of lsst/lsst-texmf", git_ref=git_ref)


@dataclass(frozen=True, slots=True)
class LinkcheckRecheckSummary:
    """The result of a scheduled link-recheck run."""

    enqueued_url_ids: list[int]
    """The ids of the due, still-referenced URLs enqueued for recheck."""

    batch_count: int
    """The number of Kafka messages the URL ids were batched into."""

    purged_check_count: int
    """The number of purged expired checks."""

    purged_url_count: int
    """The number of purged orphaned URL records."""


async def run_linkcheck_recheck(
    factory: Factory, *, batch_size: int = 100
) -> LinkcheckRecheckSummary:
    """Enqueue due link rechecks and purge expired link-check records.

    Due, still-referenced URLs are enumerated and expired records are
    purged in one transaction; the recheck messages are published to
    Kafka only after it commits.

    Parameters
    ----------
    factory
        A factory with a database session and a connected Kafka broker.
    batch_size
        The maximum number of URL ids per recheck message.

    Returns
    -------
    LinkcheckRecheckSummary
        The enqueued URL ids and purge counts.
    """
    logger = structlog.get_logger("ook")
    service = factory.create_linkcheck_service()
    async with factory.db_session.begin():
        due_urls = await service.list_due_recheck_urls()
        purge_result = await service.purge_expired_records()

    batch_count = 0
    for batch in batched(
        [due_url.id for due_url in due_urls], batch_size, strict=False
    ):
        message = RecheckUrlsMessageV1(url_ids=list(batch))
        await factory.kafka_linkcheck_publisher.publish(
            message.model_dump(mode="json")
        )
        batch_count += 1

    logger.info(
        "Completed linkcheck-recheck",
        enqueued_url_count=len(due_urls),
        batch_count=batch_count,
        purged_check_count=purge_result.check_count,
        purged_url_count=purge_result.url_count,
    )
    return LinkcheckRecheckSummary(
        enqueued_url_ids=[due_url.id for due_url in due_urls],
        batch_count=batch_count,
        purged_check_count=purge_result.check_count,
        purged_url_count=purge_result.url_count,
    )


@main.command(name="linkcheck-recheck")
@click.option(
    "--batch-size",
    default=100,
    type=click.IntRange(min=1),
    help="Maximum number of URL ids per recheck Kafka message.",
)
@run_with_asyncio
async def linkcheck_recheck(*, batch_size: int) -> None:
    """Enqueue rechecks for due link-check URLs and purge expired
    records.

    Due, still-referenced URLs are enqueued as batched Kafka messages
    for the consumer to recheck; URL records with no remaining
    occurrences and checks older than the retention period are purged.
    Intended to run as a daily cron job.
    """
    logger = structlog.get_logger("ook")
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    async with Factory.create_standalone(
        logger=logger, engine=engine
    ) as factory:
        await run_linkcheck_recheck(factory, batch_size=batch_size)
    await engine.dispose()


timespan_pattern = re.compile(
    r"((?P<weeks>\d+?)\s*(weeks|week|w))?\s*"
    r"((?P<days>\d+?)\s*(days|day|d))?\s*"
    r"((?P<hours>\d+?)\s*(hours|hour|hr|h))?\s*"
    r"((?P<minutes>\d+?)\s*(minutes|minute|mins|min|m))?\s*"
    r"((?P<seconds>\d+?)\s*(seconds|second|secs|sec|s))?$"
)
"""Regular expression pattern for a time duration."""


def parse_timedelta(text: str) -> timedelta:
    """Parse a `datetime.timedelta` from a string containing integer numbers
    of weeks, days, hours, minutes, and seconds.
    """
    m = timespan_pattern.match(text.strip())
    if m is None:
        raise ValueError(f"Could not parse a timespan from {text!r}.")
    td_args = {k: int(v) for k, v in m.groupdict().items() if v is not None}
    return timedelta(**td_args)


@main.command(name="migrate-country-codes")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be updated without making changes.",
)
@run_with_asyncio
async def migrate_country_codes(*, dry_run: bool) -> None:
    """Migrate all country codes from existing country names."""
    logger = structlog.get_logger("ook")

    engine = create_database_engine(
        config.database_url, config.database_password
    )

    async with Factory.create_standalone(
        logger=logger, engine=engine
    ) as factory:
        async with factory.db_session.begin():
            author_service = factory.create_author_service()
            await author_service.migrate_country_codes(dry_run=dry_run)

    await engine.dispose()
