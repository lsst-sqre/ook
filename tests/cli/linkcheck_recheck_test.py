"""Tests for the linkcheck-recheck CLI command."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ook.cli import main, run_linkcheck_recheck
from ook.config import config
from ook.dbschema.linkcheck import SqlCheckedUrl, SqlLinkCheck
from ook.domain.linkcheck import LinkState, LinkStatus, UrlOccurrence
from ook.factory import Factory


def test_cli_command_registered() -> None:
    """The linkcheck-recheck command is registered on the CLI group."""
    assert "linkcheck-recheck" in main.commands


@pytest.mark.asyncio
async def test_run_linkcheck_recheck(factory: Factory) -> None:
    """The scheduled recheck enqueues only due, still-referenced URLs
    in batches and purges orphaned URL records and expired checks.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    stale = now - config.linkcheck_freshness_ttl - timedelta(hours=1)
    store = factory.create_linkcheck_store()

    async with factory.db_session.begin():
        # Two stale URLs still referenced by an origin: enqueued.
        for url in (
            "https://example.com/stale-a",
            "https://example.com/stale-b",
        ):
            await store.upsert_url_state(
                LinkState(
                    url=url,
                    status=LinkStatus.ok,
                    checked_at=stale,
                    last_ok_at=stale,
                    status_code=200,
                )
            )
        await store.replace_origin_occurrences(
            origin_base_url="https://sqr-000.lsst.io",
            occurrences=[
                UrlOccurrence(
                    url="https://example.com/stale-a", origin_path="a"
                ),
                UrlOccurrence(
                    url="https://example.com/stale-b", origin_path="a"
                ),
                UrlOccurrence(
                    url="https://example.com/fresh", origin_path="a"
                ),
            ],
        )
        # A fresh referenced URL: not due, not enqueued.
        await store.upsert_url_state(
            LinkState(
                url="https://example.com/fresh",
                status=LinkStatus.ok,
                checked_at=now,
                last_ok_at=now,
                status_code=200,
            )
        )
        referenced_ids = await store.upsert_checked_urls(
            [
                "https://example.com/stale-a",
                "https://example.com/stale-b",
            ]
        )
        # An expired check whose member URL is unreferenced: both the
        # check and the orphaned URL record are purged (so the orphan is
        # never enqueued either).
        orphan_ids = await store.upsert_checked_urls(
            ["https://example.com/orphan"]
        )
        await store.create_check(
            origin_base_url="https://sqr-000.lsst.io",
            is_default_version=False,
            checked_url_ids=list(orphan_ids.values()),
            now=now - config.linkcheck_check_retention - timedelta(days=1),
        )

    summary = await run_linkcheck_recheck(factory, batch_size=1)

    assert summary.enqueued_url_ids == sorted(referenced_ids.values())
    assert summary.batch_count == 2
    assert summary.purged_check_count == 1
    assert summary.purged_url_count == 1

    async with factory.db_session.begin():
        remaining_urls = (
            (await factory.db_session.execute(select(SqlCheckedUrl.url)))
            .scalars()
            .all()
        )
        assert set(remaining_urls) == {
            "https://example.com/stale-a",
            "https://example.com/stale-b",
            "https://example.com/fresh",
        }
        remaining_checks = (
            (await factory.db_session.execute(select(SqlLinkCheck.id)))
            .scalars()
            .all()
        )
        assert remaining_checks == []
