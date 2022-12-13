"""Administrative command-line interface."""

__all__ = ["main", "help", "run"]

from pathlib import Path
from typing import Any, Union

import click
from aiohttp.web import run_app
from algoliasearch.search_client import SearchClient

from ook.app import create_app
from ook.config import Configuration
from ook.ingest.workflows.manualstub import add_manual_doc_stub
from ook.utils import run_with_asyncio

# Add -h as a help shortcut option
CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(message="%(version)s")
@click.pass_context
def main(ctx: click.Context) -> None:
    """ook

    Administrative command-line interface for ook.
    """
    # Subcommands should use the click.pass_obj decorator to get this
    # ctx object as the first argument.
    ctx.obj = {}


@main.command()
@click.argument("topic", default=None, required=False, nargs=1)
@click.pass_context
def help(ctx: click.Context, topic: Union[None, str], **kw: Any) -> None:
    """Show help for any command."""
    # The help command implementation is taken from
    # https://www.burgundywall.com/post/having-click-help-subcommand
    if topic:
        if topic in main.commands:
            click.echo(main.commands[topic].get_help(ctx))
        else:
            raise click.UsageError(f"Unknown help topic {topic}", ctx)
    else:
        assert ctx.parent
        click.echo(ctx.parent.get_help())


@main.command()
@click.option(
    "--port", default=8080, type=int, help="Port to run the application on."
)
@click.pass_context
def run(ctx: click.Context, port: int) -> None:
    """Run the application (for production)."""
    app = create_app()
    run_app(app, port=port)


@main.command()
@click.option(
    "--dataset", required=True, type=click.Path(exists=True, path_type=Path)
)
@click.pass_context
@run_with_asyncio
async def upload_doc_stub(ctx: click.Context, dataset: Path) -> None:
    """Upload a stub record for a document that can't be normally indexed."""
    config = Configuration()
    assert config.algolia_document_index_name is not None
    assert config.algolia_app_id is not None
    assert config.algolia_api_key is not None

    async with SearchClient.create(
        config.algolia_app_id,
        api_key=config.algolia_api_key.get_secret_value(),
    ) as client:
        index = client.init_index(config.algolia_document_index_name)
        await add_manual_doc_stub(index, dataset.read_text())
