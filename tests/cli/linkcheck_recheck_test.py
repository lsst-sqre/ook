"""Tests for the linkcheck-recheck CLI command."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from ook.cli import main, run_linkcheck_recheck
from ook.config import config
from ook.dbschema.linkcheck import SqlCheckedUrl, SqlLinkCheck
from ook.domain.kafka import RecheckUrlsMessageV1
from ook.domain.linkcheck import LinkState, LinkStatus, UrlOccurrence
from ook.factory import Factory


class RecordingPublisher:
    """A stand-in for the link-check Kafka publisher that records the
    recheck messages it is asked to publish.
    """

    def __init__(self) -> None:
        self.messages: list[RecheckUrlsMessageV1] = []

    async def publish(self, message: dict[str, Any], **kwargs: Any) -> None:
        self.messages.append(RecheckUrlsMessageV1.model_validate(message))


@pytest.fixture
def recheck_publisher(monkeypatch: pytest.MonkeyPatch) -> RecordingPublisher:
    """Capture the recheck messages a run publishes instead of sending
    them to Kafka.
    """
    publisher = RecordingPublisher()
    monkeypatch.setattr(
        Factory,
        "kafka_linkcheck_publisher",
        property(lambda self: publisher),
    )
    return publisher


def test_cli_command_registered() -> None:
    """The linkcheck-recheck command is registered on the CLI group."""
    assert "linkcheck-recheck" in main.commands


def test_cli_parses_forcing_options() -> None:
    """The forcing options are repeatable and accept any HTTP status code
    and the broken, blocked, and failing statuses.
    """
    command = main.commands["linkcheck-recheck"]
    ctx = command.make_context(
        "linkcheck-recheck",
        [
            "--include-status-code",
            "100",
            "--include-status-code",
            "599",
            "--include-status",
            "broken",
            "--include-status",
            "blocked",
            "--include-status",
            "failing",
        ],
    )
    assert ctx.params["include_status_codes"] == (100, 599)
    assert ctx.params["include_statuses"] == (
        LinkStatus.broken,
        LinkStatus.blocked,
        LinkStatus.failing,
    )


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            ["--include-status", "ok"],
            "'ok' is not one of 'broken', 'blocked', 'failing'",
        ),
        (["--include-status-code", "600"], "100<=x<=599"),
        (["--include-status-code", "99"], "100<=x<=599"),
    ],
)
def test_cli_rejects_invalid_forcing_options(
    args: list[str], message: str
) -> None:
    """Invalid forcing options are rejected with a usage error before
    anything is enqueued.
    """
    result = CliRunner().invoke(main.commands["linkcheck-recheck"], args)
    assert result.exit_code == 2
    assert message in result.output


@pytest.mark.asyncio
async def test_run_linkcheck_recheck_include_status_code(
    factory: Factory, recheck_publisher: RecordingPublisher
) -> None:
    """Forcing a status code enqueues the still-referenced URLs stored
    with that code even though their recheck time has not arrived.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    store = factory.create_linkcheck_store()

    async with factory.db_session.begin():
        for url, status, status_code in (
            ("https://docs.example.com/misdirected", LinkStatus.blocked, 421),
            ("https://docs.example.com/orphan", LinkStatus.blocked, 421),
            ("https://docs.example.com/throttled", LinkStatus.blocked, 429),
        ):
            await store.upsert_url_state(
                LinkState(
                    url=url,
                    status=status,
                    date_checked=now,
                    date_last_ok=now - timedelta(days=1),
                    status_code=status_code,
                    date_next_check=now + timedelta(hours=6),
                )
            )
        # The orphan 421 no longer occurs on any origin page.
        await store.replace_origin_occurrences(
            origin_base_url="https://sqr-000.lsst.io",
            occurrences=[
                UrlOccurrence(
                    url="https://docs.example.com/misdirected",
                    origin_path="a",
                ),
                UrlOccurrence(
                    url="https://docs.example.com/throttled",
                    origin_path="a",
                ),
            ],
        )
        ids = await store.upsert_checked_urls(
            ["https://docs.example.com/misdirected"]
        )

    summary = await run_linkcheck_recheck(factory, include_status_codes=[421])

    forced_id = ids["https://docs.example.com/misdirected"]
    assert summary.enqueued_url_ids == [forced_id]
    assert summary.forced_url_count == 1
    assert summary.batch_count == 1
    assert recheck_publisher.messages == [
        RecheckUrlsMessageV1(url_ids=[forced_id], force=True)
    ]


@pytest.mark.asyncio
async def test_run_linkcheck_recheck_include_statuses(
    factory: Factory, recheck_publisher: RecordingPublisher
) -> None:
    """Forcing several statuses enqueues their union, and a URL that is
    both forced and due is enqueued once, with the due URLs.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    later = now + timedelta(hours=6)
    store = factory.create_linkcheck_store()

    urls = {
        # Matches a filter but is not due: forced.
        "https://example.com/broken": (LinkStatus.broken, later),
        "https://example.com/blocked": (LinkStatus.blocked, later),
        # Matches a filter and is due: enqueued once, unforced.
        "https://example.com/broken-due": (
            LinkStatus.broken,
            now - timedelta(minutes=5),
        ),
        # Matches no filter and is not due: not enqueued.
        "https://example.com/failing": (LinkStatus.failing, later),
    }
    async with factory.db_session.begin():
        for url, (status, date_next_check) in urls.items():
            await store.upsert_url_state(
                LinkState(
                    url=url,
                    status=status,
                    date_checked=now - timedelta(hours=1),
                    status_code=404,
                    date_next_check=date_next_check,
                )
            )
        await store.replace_origin_occurrences(
            origin_base_url="https://sqr-000.lsst.io",
            occurrences=[
                UrlOccurrence(url=url, origin_path="a") for url in urls
            ],
        )
        ids = await store.upsert_checked_urls(list(urls))

    summary = await run_linkcheck_recheck(
        factory, include_statuses=[LinkStatus.broken, LinkStatus.blocked]
    )

    due_ids = [ids["https://example.com/broken-due"]]
    forced_ids = sorted(
        [ids["https://example.com/broken"], ids["https://example.com/blocked"]]
    )
    assert summary.enqueued_url_ids == due_ids + forced_ids
    assert summary.forced_url_count == 2
    assert summary.batch_count == 2
    assert recheck_publisher.messages == [
        RecheckUrlsMessageV1(url_ids=due_ids, force=False),
        RecheckUrlsMessageV1(url_ids=forced_ids, force=True),
    ]


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
                    date_checked=stale,
                    date_last_ok=stale,
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
                date_checked=now,
                date_last_ok=now,
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
