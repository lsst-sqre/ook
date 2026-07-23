"""Tests for the refresh-intersphinx CLI command."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import respx
from httpx import Response

from ook.cli import main, run_refresh_intersphinx
from ook.domain.intersphinx import IntersphinxInventory, InventoryFetchStatus
from ook.factory import Factory

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
