"""Administrative command-line interface."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import click
import structlog
from algoliasearch.search_client import SearchClient
from safir.asyncio import run_with_asyncio
from safir.logging import configure_logging

from ook.config import config
from ook.domain.algoliarecord import MinimalDocumentModel
from ook.factory import Factory
from ook.services.algoliadocindex import AlgoliaDocIndexService

__all__ = ["help", "main", "upload_doc_stub"]

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
    async with Factory.create_standalone(logger=logger) as factory:
        algolia_audit_service = factory.create_algolia_audit_service()
        await algolia_audit_service.audit_missing_documents(
            ingest_missing=reingest
        )


@main.command(name="ingest-updated")
@click.option(
    "--window",
    default="2d",
    help="Time window to check for document updates. E.g. 2d, 1w, 1m, 1y.",
)
@run_with_asyncio
async def ingest_updated(*, window: str) -> None:
    logger = structlog.get_logger("ook")
    window_timedelta = parse_timedelta(window)
    async with Factory.create_standalone(logger=logger) as factory:
        classification_service = factory.create_classification_service()
        await classification_service.queue_ingest_for_updated_ltd_projects(
            window_timedelta
        )


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
