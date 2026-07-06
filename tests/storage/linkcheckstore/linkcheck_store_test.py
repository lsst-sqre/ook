"""Tests for the LinkCheckStore."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ook.dbschema.linkcheck import (
    SqlCheckedUrl,
    SqlLinkCheck,
    SqlLinkCheckUrl,
    SqlUrlOccurrence,
)
from ook.domain.linkcheck import LinkState, LinkStatus, UrlOccurrence
from ook.factory import Factory


async def _get_occurrences(
    factory: Factory, ltd_slug: str
) -> set[tuple[str, str]]:
    """Get a project's occurrence set as (url, path) tuples."""
    rows = (
        await factory.db_session.execute(
            select(SqlCheckedUrl.url, SqlUrlOccurrence.path)
            .join(
                SqlCheckedUrl,
                SqlCheckedUrl.id == SqlUrlOccurrence.checked_url_id,
            )
            .where(SqlUrlOccurrence.ltd_slug == ltd_slug)
        )
    ).all()
    return {(row.url, row.path) for row in rows}


@pytest.mark.asyncio
async def test_upsert_checked_urls(factory: Factory) -> None:
    """Upserting URLs creates rows once and returns stable IDs."""
    async with factory.db_session.begin():
        store = factory.create_linkcheck_store()

        urls = [
            "https://example.com/page",
            "https://example.org/other",
        ]
        ids = await store.upsert_checked_urls(urls)
        assert set(ids.keys()) == set(urls)

        # Upserting again (with an overlapping new URL) is idempotent for
        # existing URLs and creates only the new row.
        ids2 = await store.upsert_checked_urls(
            [*urls, "https://example.net/new"]
        )
        assert (
            ids2["https://example.com/page"] == ids["https://example.com/page"]
        )
        assert (
            ids2["https://example.org/other"]
            == ids["https://example.org/other"]
        )
        assert "https://example.net/new" in ids2


@pytest.mark.asyncio
async def test_url_state_roundtrip(factory: Factory) -> None:
    """A LinkState upsert round-trips through single-URL lookup."""
    async with factory.db_session.begin():
        store = factory.create_linkcheck_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        url = "https://example.com/moved"

        # A URL with no record has no state.
        assert await store.get_url_state(url) is None

        # A never-checked URL record has no state yet.
        await store.upsert_checked_urls([url])
        assert await store.get_url_state(url) is None

        state = LinkState(
            url=url,
            status=LinkStatus.redirected,
            checked_at=now,
            last_ok_at=now,
            failing_since=None,
            failure_count=0,
            status_code=200,
            redirect_status_code=301,
            redirect_url="https://example.com/new-location",
            error=None,
            next_check_at=None,
        )
        await store.upsert_url_state(state)
        assert await store.get_url_state(url) == state

        # Updating the state of an existing URL overwrites all fields.
        failing_state = LinkState(
            url=url,
            status=LinkStatus.failing,
            checked_at=now + timedelta(hours=1),
            last_ok_at=now,
            failing_since=now + timedelta(hours=1),
            failure_count=1,
            status_code=503,
            redirect_status_code=None,
            redirect_url=None,
            error="503 Service Unavailable",
            next_check_at=now + timedelta(hours=2),
        )
        await store.upsert_url_state(failing_state)
        assert await store.get_url_state(url) == failing_state

        # Upserting a state for an unknown URL creates the record.
        new_url = "https://example.org/fresh"
        new_state = LinkState(
            url=new_url,
            status=LinkStatus.ok,
            checked_at=now,
            last_ok_at=now,
        )
        await store.upsert_url_state(new_state)
        assert await store.get_url_state(new_url) == new_state


@pytest.mark.asyncio
async def test_create_check_with_membership(factory: Factory) -> None:
    """Creating a check records its metadata and URL membership."""
    async with factory.db_session.begin():
        store = factory.create_linkcheck_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)

        ids = await store.upsert_checked_urls(
            ["https://example.com/a", "https://example.com/b"]
        )
        check_id = await store.create_check(
            ltd_slug="sqr-000",
            default_branch=True,
            checked_url_ids=list(ids.values()),
            now=now,
        )

        check = (
            await factory.db_session.execute(
                select(SqlLinkCheck).where(SqlLinkCheck.id == check_id)
            )
        ).scalar_one()
        assert check.ltd_slug == "sqr-000"
        assert check.default_branch is True
        assert check.status == "pending"
        assert check.date_created == now
        assert check.date_completed is None

        member_url_ids = (
            (
                await factory.db_session.execute(
                    select(SqlLinkCheckUrl.checked_url_id).where(
                        SqlLinkCheckUrl.check_id == check_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(member_url_ids) == set(ids.values())


@pytest.mark.asyncio
async def test_replace_project_occurrences(factory: Factory) -> None:
    """A project's occurrence set is replaced without affecting other
    projects.
    """
    async with factory.db_session.begin():
        store = factory.create_linkcheck_store()

        # Occurrences of URLs that have no records yet create them,
        # and duplicate occurrences collapse to one row.
        await store.replace_project_occurrences(
            ltd_slug="sqr-000",
            occurrences=[
                UrlOccurrence(url="https://example.com/a", path="index"),
                UrlOccurrence(url="https://example.com/a", path="index"),
                UrlOccurrence(url="https://example.com/a", path="guide"),
                UrlOccurrence(url="https://example.com/b", path="index"),
            ],
        )
        await store.replace_project_occurrences(
            ltd_slug="sqr-001",
            occurrences=[
                UrlOccurrence(url="https://example.com/a", path="notes"),
            ],
        )
        assert await _get_occurrences(factory, "sqr-000") == {
            ("https://example.com/a", "index"),
            ("https://example.com/a", "guide"),
            ("https://example.com/b", "index"),
        }

        # Replacing sqr-000's set removes stale occurrences and leaves
        # sqr-001 untouched.
        await store.replace_project_occurrences(
            ltd_slug="sqr-000",
            occurrences=[
                UrlOccurrence(url="https://example.com/b", path="changelog"),
            ],
        )
        assert await _get_occurrences(factory, "sqr-000") == {
            ("https://example.com/b", "changelog"),
        }
        assert await _get_occurrences(factory, "sqr-001") == {
            ("https://example.com/a", "notes"),
        }

        # Replacing with an empty set clears the project's occurrences.
        await store.replace_project_occurrences(
            ltd_slug="sqr-000", occurrences=[]
        )
        assert await _get_occurrences(factory, "sqr-000") == set()


@pytest.mark.asyncio
async def test_get_due_urls(factory: Factory) -> None:
    """Due-URL enumeration returns never-checked, ladder-due, and
    stale URLs, but not fresh or unsupported ones.
    """
    async with factory.db_session.begin():
        store = factory.create_linkcheck_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        ttl = timedelta(hours=24)

        def make_state(
            url: str,
            status: LinkStatus,
            checked_at: datetime,
            next_check_at: datetime | None = None,
        ) -> LinkState:
            return LinkState(
                url=url,
                status=status,
                checked_at=checked_at,
                last_ok_at=checked_at,
                next_check_at=next_check_at,
            )

        # Never checked: due.
        ids = await store.upsert_checked_urls(["https://due.example.com/new"])
        # On the retry ladder with a recheck time in the past: due.
        await store.upsert_url_state(
            make_state(
                "https://due.example.com/ladder",
                LinkStatus.failing,
                now - timedelta(hours=2),
                next_check_at=now - timedelta(hours=1),
            )
        )
        # On the retry ladder with a recheck time in the future: not due.
        await store.upsert_url_state(
            make_state(
                "https://fresh.example.com/ladder",
                LinkStatus.failing,
                now - timedelta(hours=1),
                next_check_at=now + timedelta(hours=3),
            )
        )
        # Checked within the freshness TTL: not due.
        await store.upsert_url_state(
            make_state(
                "https://fresh.example.com/ok",
                LinkStatus.ok,
                now - timedelta(hours=1),
            )
        )
        # Last checked longer ago than the freshness TTL: due.
        await store.upsert_url_state(
            make_state(
                "https://due.example.com/stale",
                LinkStatus.ok,
                now - timedelta(hours=25),
            )
        )
        # Unsupported URLs are never due, no matter how stale.
        await store.upsert_url_state(
            make_state(
                "mailto:someone@example.com",
                LinkStatus.unsupported,
                now - timedelta(days=30),
            )
        )

        due = await store.get_due_urls(now=now, ttl=ttl)
        assert {d.url for d in due} == {
            "https://due.example.com/new",
            "https://due.example.com/ladder",
            "https://due.example.com/stale",
        }
        due_by_url = {d.url: d.id for d in due}
        assert (
            due_by_url["https://due.example.com/new"]
            == ids["https://due.example.com/new"]
        )

        # A limit caps the number of returned URLs.
        limited = await store.get_due_urls(now=now, ttl=ttl, limit=2)
        assert len(limited) == 2
