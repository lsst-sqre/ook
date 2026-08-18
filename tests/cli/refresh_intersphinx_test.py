"""Tests for the refresh-intersphinx CLI command."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import click
import pytest
import respx
from httpx import Response

from ook.cli import main, report_refresh_intersphinx, run_refresh_intersphinx
from ook.domain.intersphinx import IntersphinxInventory, InventoryFetchStatus
from ook.factory import Factory
from ook.services.intersphinx import IntersphinxRefreshSummary

INVENTORY_URL = "https://docs.example.com/en/latest/objects.inv"


def test_cli_command_registered() -> None:
    """The refresh-intersphinx command is registered on the CLI group."""
    assert "refresh-intersphinx" in main.commands


@pytest.mark.asyncio
async def test_run_refresh_intersphinx(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """The scheduled refresh revalidates a stale, still-active inventory and
    commits the result.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        await store.upsert_inventory(
            IntersphinxInventory(
                url=INVENTORY_URL,
                content=b"objects.inv payload",
                content_type="application/octet-stream",
                etag='"stored-etag"',
                last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
                date_fetched=now - timedelta(hours=2),
                date_requested=now - timedelta(days=1),
                last_fetch_status=InventoryFetchStatus.success,
                last_fetch_error=None,
                date_refresh_failed=None,
            )
        )
    route = respx_mock.get(INVENTORY_URL).mock(return_value=Response(304))

    summary = await run_refresh_intersphinx(factory)

    assert route.call_count == 1
    assert summary.considered == 1
    assert summary.revalidated == 1

    # The committed row keeps its content and carries a bumped fetch time.
    async with factory.db_session.begin():
        store = factory.create_intersphinx_inventory_store()
        stored = await store.get_inventory(INVENTORY_URL)
    assert stored is not None
    assert stored.content == b"objects.inv payload"
    assert stored.date_fetched is not None
    assert stored.date_fetched > now - timedelta(hours=1)


def test_report_refresh_intersphinx_reports_a_clean_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A run whose failures were all recorded reports its counts and exits
    successfully.
    """
    report_refresh_intersphinx(
        IntersphinxRefreshSummary(
            considered=3,
            refreshed=1,
            revalidated=1,
            superseded=0,
            failed=1,
            unrecorded_failures=0,
        )
    )

    assert "3 considered" in capsys.readouterr().out


def test_report_refresh_intersphinx_fails_on_an_unrecorded_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A refresh failure whose bookkeeping write failed fails the run.

    An inventory left without its backoff marker heads the next run's due
    list and fails again, so the CronJob has to exit nonzero — but only
    after the batch has run to the end and reported its counts, since a
    bookkeeping error is not a reason to abandon the other inventories.
    """
    with pytest.raises(click.ClickException):
        report_refresh_intersphinx(
            IntersphinxRefreshSummary(
                considered=3,
                refreshed=1,
                revalidated=1,
                superseded=0,
                failed=1,
                unrecorded_failures=1,
            )
        )

    assert "1 unrecorded" in capsys.readouterr().out
