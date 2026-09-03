"""Tests for the ingest-intersphinx CLI command."""

from __future__ import annotations

import click
import pytest
import respx
import sphobjinv
from httpx import Response

from ook.cli import main, report_ingest_intersphinx, run_ingest_intersphinx
from ook.domain.base32id import generate_base32_id, validate_base32_id
from ook.domain.intersphinxsources import SourceIngestStatus
from ook.factory import Factory
from ook.services.ingest.intersphinx import (
    IntersphinxIngestSummary,
    SourceIngestResult,
)

INVENTORY_URL = "https://docs.example.com/en/latest/objects.inv"
"""The registered inventory URL the run visits."""


def _inventory() -> bytes:
    """Build a one-object ``objects.inv`` payload."""
    inventory = sphobjinv.Inventory()
    inventory.project = "Example"
    inventory.version = "1.0"
    inventory.objects.append(
        sphobjinv.DataObjStr(
            name="pkg.Thing",
            domain="py",
            role="class",
            priority="1",
            uri="api.html#pkg.Thing",
            dispname="-",
        )
    )
    return sphobjinv.compress(inventory.data_file())


def _result(status: SourceIngestStatus) -> SourceIngestResult:
    """Build a source outcome for the reporting tests."""
    return SourceIngestResult(
        source_id=validate_base32_id(generate_base32_id()),
        url=INVENTORY_URL,
        title="Example docs",
        status=status,
        entity_count=1,
        link_count=1,
        pruned_count=0,
        error=None if status is SourceIngestStatus.success else "boom",
    )


def test_cli_command_registered() -> None:
    """The ingest-intersphinx command is registered on the CLI group."""
    assert "ingest-intersphinx" in main.commands


@pytest.mark.asyncio
async def test_run_ingest_intersphinx(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """The scheduled ingest stores an enabled source's links and commits."""
    respx_mock.get(INVENTORY_URL).mock(
        return_value=Response(200, content=_inventory())
    )
    async with factory.db_session.begin():
        await factory.create_intersphinx_source_store().add_source(
            url=INVENTORY_URL, title="Example docs"
        )

    summary = await run_ingest_intersphinx(factory)

    assert summary.succeeded == 1
    assert summary.failed == 0

    # The committed transaction holds the entity and its link.
    entity = await factory.create_intersphinx_entity_store().get_entity(
        "py", "pkg.Thing"
    )
    assert entity is not None
    assert [link.html_url for link in entity.links] == [
        "https://docs.example.com/en/latest/api.html#pkg.Thing"
    ]


def test_report_ingest_intersphinx_reports_a_clean_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A run in which every source succeeded reports its counts and exits
    successfully.
    """
    report_ingest_intersphinx(
        IntersphinxIngestSummary(results=[_result(SourceIngestStatus.success)])
    )

    assert "1 succeeded" in capsys.readouterr().out


def test_report_ingest_intersphinx_fails_on_a_failed_source(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A source that could not be ingested fails the command.

    The run itself deliberately continues past a failing site, so the
    nonzero exit comes after the counts are printed: the CronJob has to show
    red for a site Ook has stopped refreshing, without that costing the
    other sites their ingest.
    """
    with pytest.raises(click.ClickException):
        report_ingest_intersphinx(
            IntersphinxIngestSummary(
                results=[
                    _result(SourceIngestStatus.success),
                    _result(SourceIngestStatus.failure),
                ]
            )
        )

    assert "1 failed" in capsys.readouterr().out
