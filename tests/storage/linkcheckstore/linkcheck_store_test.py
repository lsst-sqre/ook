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
from ook.domain.linkcheck import (
    CheckRunStatus,
    LinkState,
    LinkStatus,
    UrlOccurrence,
)
from ook.factory import Factory


async def _get_occurrences(
    factory: Factory, origin_base_url: str
) -> set[tuple[str, str]]:
    """Get an origin's occurrence set as (url, origin_path) tuples."""
    rows = (
        await factory.db_session.execute(
            select(SqlCheckedUrl.url, SqlUrlOccurrence.origin_path)
            .join(
                SqlCheckedUrl,
                SqlCheckedUrl.id == SqlUrlOccurrence.checked_url_id,
            )
            .where(SqlUrlOccurrence.origin_base_url == origin_base_url)
        )
    ).all()
    return {(row.url, row.origin_path) for row in rows}


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

        # A blocked state round-trips its consecutive-blocked counter.
        blocked_state = LinkState(
            url=url,
            status=LinkStatus.blocked,
            checked_at=now + timedelta(hours=2),
            last_ok_at=now,
            failing_since=now + timedelta(hours=1),
            failure_count=1,
            consecutive_blocked_count=3,
            status_code=403,
            error="HTTP 403 (likely blocked by bot protection)",
            next_check_at=now + timedelta(hours=6),
        )
        await store.upsert_url_state(blocked_state)
        assert await store.get_url_state(url) == blocked_state

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
            origin_base_url="https://sqr-000.lsst.io",
            is_default_version=True,
            checked_url_ids=list(ids.values()),
            now=now,
        )

        check = (
            await factory.db_session.execute(
                select(SqlLinkCheck).where(SqlLinkCheck.id == check_id)
            )
        ).scalar_one()
        assert check.origin_base_url == "https://sqr-000.lsst.io"
        assert check.is_default_version is True
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
async def test_create_check_persists_origin_paths(factory: Factory) -> None:
    """create_check records each URL's submitted origin paths on its
    membership row, and get_check returns them per URL, sorted and
    de-duplicated.
    """
    async with factory.db_session.begin():
        store = factory.create_linkcheck_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)

        ids = await store.upsert_checked_urls(
            ["https://example.com/a", "https://example.com/b"], now=now
        )
        check_id = await store.create_check(
            origin_base_url="https://sqr-000.lsst.io",
            is_default_version=False,
            checked_url_ids=list(ids.values()),
            origin_paths={
                # Unsorted with a duplicate: stored sorted and unique.
                ids["https://example.com/a"]: ["index", "guide", "index"],
                # A URL with no submitted paths has an empty array.
                ids["https://example.com/b"]: [],
            },
            now=now,
        )

        record = await store.get_check(check_id)
        assert record is not None
        by_url = {u.url: u for u in record.urls}
        assert by_url["https://example.com/a"].origin_paths == [
            "guide",
            "index",
        ]
        assert by_url["https://example.com/b"].origin_paths == []


@pytest.mark.asyncio
async def test_create_check_without_origin_paths(factory: Factory) -> None:
    """Omitting origin_paths defaults every membership row to an empty
    array.
    """
    async with factory.db_session.begin():
        store = factory.create_linkcheck_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)

        ids = await store.upsert_checked_urls(
            ["https://example.com/a"], now=now
        )
        check_id = await store.create_check(
            origin_base_url="https://sqr-000.lsst.io",
            is_default_version=False,
            checked_url_ids=list(ids.values()),
            now=now,
        )

        record = await store.get_check(check_id)
        assert record is not None
        assert record.urls[0].origin_paths == []


@pytest.mark.asyncio
async def test_update_check_status(factory: Factory) -> None:
    """A check's processing status advances pending → in_progress →
    complete, recording the completion time.
    """
    async with factory.db_session.begin():
        store = factory.create_linkcheck_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)

        ids = await store.upsert_checked_urls(["https://example.com/a"])
        check_id = await store.create_check(
            origin_base_url="https://sqr-000.lsst.io",
            is_default_version=True,
            checked_url_ids=list(ids.values()),
            now=now,
        )

        record = await store.get_check(check_id)
        assert record is not None
        assert record.status is CheckRunStatus.pending
        assert record.date_completed is None

        await store.update_check_status(check_id, CheckRunStatus.in_progress)
        record = await store.get_check(check_id)
        assert record is not None
        assert record.status is CheckRunStatus.in_progress
        assert record.date_completed is None

        completed_at = now + timedelta(minutes=5)
        await store.update_check_status(
            check_id, CheckRunStatus.complete, now=completed_at
        )
        record = await store.get_check(check_id)
        assert record is not None
        assert record.status is CheckRunStatus.complete
        assert record.date_completed == completed_at


@pytest.mark.asyncio
async def test_get_check(factory: Factory) -> None:
    """Getting a check returns its metadata and member URL states."""
    async with factory.db_session.begin():
        store = factory.create_linkcheck_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)

        # An unknown check ID resolves to None.
        assert await store.get_check(123456789) is None

        # One member URL has a state; the other has never been checked.
        checked_state = LinkState(
            url="https://example.com/checked",
            status=LinkStatus.redirected,
            checked_at=now,
            last_ok_at=now,
            status_code=200,
            redirect_status_code=301,
            redirect_url="https://example.com/new-location",
        )
        await store.upsert_url_state(checked_state, now=now)
        ids = await store.upsert_checked_urls(
            ["https://example.com/checked", "https://example.com/unchecked"],
            now=now,
        )
        check_id = await store.create_check(
            origin_base_url="https://sqr-000.lsst.io",
            is_default_version=False,
            checked_url_ids=list(ids.values()),
            now=now,
        )

        record = await store.get_check(check_id)
        assert record is not None
        assert record.id == check_id
        assert record.origin_base_url == "https://sqr-000.lsst.io"
        assert record.is_default_version is False
        assert record.status is CheckRunStatus.pending
        assert record.date_created == now
        assert record.date_completed is None

        # Member URLs are ordered by URL.
        assert [u.url for u in record.urls] == [
            "https://example.com/checked",
            "https://example.com/unchecked",
        ]
        checked, unchecked = record.urls
        assert checked.status is LinkStatus.redirected
        assert checked.last_checked_at == now
        assert checked.status_code == 200
        assert checked.redirect_status_code == 301
        assert checked.redirect_url == "https://example.com/new-location"
        assert checked.error is None
        assert unchecked.status is None
        assert unchecked.last_checked_at is None


@pytest.mark.asyncio
async def test_get_url_states(factory: Factory) -> None:
    """Batch state lookup returns states only for checked URLs."""
    async with factory.db_session.begin():
        store = factory.create_linkcheck_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)

        state = LinkState(
            url="https://example.com/checked",
            status=LinkStatus.ok,
            checked_at=now,
            last_ok_at=now,
        )
        await store.upsert_url_state(state, now=now)
        await store.upsert_checked_urls(
            ["https://example.com/unchecked"], now=now
        )

        states = await store.get_url_states(
            [
                "https://example.com/checked",
                "https://example.com/unchecked",
                "https://example.com/unknown",
            ]
        )
        assert states == {"https://example.com/checked": state}


@pytest.mark.asyncio
async def test_replace_origin_occurrences(factory: Factory) -> None:
    """An origin's occurrence set is replaced without affecting other
    origins.
    """
    async with factory.db_session.begin():
        store = factory.create_linkcheck_store()

        # Occurrences of URLs that have no records yet create them,
        # and duplicate occurrences collapse to one row.
        await store.replace_origin_occurrences(
            origin_base_url="https://sqr-000.lsst.io",
            occurrences=[
                UrlOccurrence(
                    url="https://example.com/a", origin_path="index"
                ),
                UrlOccurrence(
                    url="https://example.com/a", origin_path="index"
                ),
                UrlOccurrence(
                    url="https://example.com/a", origin_path="guide"
                ),
                UrlOccurrence(
                    url="https://example.com/b", origin_path="index"
                ),
            ],
        )
        await store.replace_origin_occurrences(
            origin_base_url="https://sqr-001.lsst.io",
            occurrences=[
                UrlOccurrence(
                    url="https://example.com/a", origin_path="notes"
                ),
            ],
        )
        assert await _get_occurrences(factory, "https://sqr-000.lsst.io") == {
            ("https://example.com/a", "index"),
            ("https://example.com/a", "guide"),
            ("https://example.com/b", "index"),
        }

        # Replacing sqr-000's set removes stale occurrences and
        # leaves sqr-001 untouched.
        await store.replace_origin_occurrences(
            origin_base_url="https://sqr-000.lsst.io",
            occurrences=[
                UrlOccurrence(
                    url="https://example.com/b", origin_path="changelog"
                ),
            ],
        )
        assert await _get_occurrences(factory, "https://sqr-000.lsst.io") == {
            ("https://example.com/b", "changelog"),
        }
        assert await _get_occurrences(factory, "https://sqr-001.lsst.io") == {
            ("https://example.com/a", "notes"),
        }

        # Replacing with an empty set clears the origin's occurrences.
        await store.replace_origin_occurrences(
            origin_base_url="https://sqr-000.lsst.io", occurrences=[]
        )
        assert (
            await _get_occurrences(factory, "https://sqr-000.lsst.io") == set()
        )


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
        # Broken with a slow-recheck time in the past: due, so a
        # recovered link can heal without waiting for resubmission.
        await store.upsert_url_state(
            make_state(
                "https://due.example.com/broken",
                LinkStatus.broken,
                now - timedelta(hours=2),
                next_check_at=now - timedelta(hours=1),
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
            "https://due.example.com/broken",
        }
        due_by_url = {d.url: d.id for d in due}
        assert (
            due_by_url["https://due.example.com/new"]
            == ids["https://due.example.com/new"]
        )

        # A limit caps the number of returned URLs.
        limited = await store.get_due_urls(now=now, ttl=ttl, limit=2)
        assert len(limited) == 2


@pytest.mark.asyncio
async def test_get_due_urls_referenced_only(factory: Factory) -> None:
    """Referenced-only due-URL enumeration excludes URLs that no longer
    occur on any origin page.
    """
    async with factory.db_session.begin():
        store = factory.create_linkcheck_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        ttl = timedelta(hours=24)

        # A never-checked URL occurring on an origin page: due.
        await store.replace_origin_occurrences(
            origin_base_url="https://sqr-000.lsst.io",
            occurrences=[
                UrlOccurrence(
                    url="https://due.example.com/referenced",
                    origin_path="index",
                )
            ],
        )
        # A never-checked URL with no occurrences: due, but excluded
        # from referenced-only enumeration.
        await store.upsert_checked_urls(
            ["https://due.example.com/unreferenced"]
        )

        due = await store.get_due_urls(now=now, ttl=ttl, referenced_only=True)
        assert {d.url for d in due} == {"https://due.example.com/referenced"}

        # The default enumeration still includes unreferenced URLs.
        due_all = await store.get_due_urls(now=now, ttl=ttl)
        assert {d.url for d in due_all} == {
            "https://due.example.com/referenced",
            "https://due.example.com/unreferenced",
        }


@pytest.mark.asyncio
async def test_purge_expired_checks(factory: Factory) -> None:
    """Purging expired checks deletes checks (and their memberships)
    older than the retention period while keeping recent checks.
    """
    async with factory.db_session.begin():
        store = factory.create_linkcheck_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        retention = timedelta(days=30)

        ids = await store.upsert_checked_urls(["https://example.com/a"])
        old_check_id = await store.create_check(
            origin_base_url="https://sqr-000.lsst.io",
            is_default_version=False,
            checked_url_ids=list(ids.values()),
            now=now - timedelta(days=31),
        )
        recent_check_id = await store.create_check(
            origin_base_url="https://sqr-000.lsst.io",
            is_default_version=False,
            checked_url_ids=list(ids.values()),
            now=now - timedelta(days=1),
        )

        purged = await store.purge_expired_checks(now=now, retention=retention)
        assert purged == 1
        assert await store.get_check(old_check_id) is None
        assert await store.get_check(recent_check_id) is not None

        # The expired check's membership rows are gone as well.
        memberships = (
            (
                await factory.db_session.execute(
                    select(SqlLinkCheckUrl.check_id).where(
                        SqlLinkCheckUrl.check_id == old_check_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert memberships == []


@pytest.mark.asyncio
async def test_purge_orphan_urls(factory: Factory) -> None:
    """Purging orphan URLs deletes records with no remaining
    occurrences and no check membership, keeping referenced URLs.
    """
    async with factory.db_session.begin():
        store = factory.create_linkcheck_store()
        now = datetime.now(tz=UTC).replace(microsecond=0)

        # A URL occurring on an origin page is kept.
        await store.replace_origin_occurrences(
            origin_base_url="https://sqr-000.lsst.io",
            occurrences=[
                UrlOccurrence(
                    url="https://example.com/referenced", origin_path="a"
                )
            ],
        )
        # A URL that is only a member of a (recent) check is kept, so
        # active check reports never lose members mid-poll.
        member_ids = await store.upsert_checked_urls(
            ["https://example.com/member"]
        )
        await store.create_check(
            origin_base_url="https://sqr-000.lsst.io",
            is_default_version=False,
            checked_url_ids=list(member_ids.values()),
            now=now,
        )
        # A URL with neither occurrences nor membership is purged.
        await store.upsert_checked_urls(["https://example.com/orphan"])

        purged = await store.purge_orphan_urls()
        assert purged == 1

        remaining = (
            (await factory.db_session.execute(select(SqlCheckedUrl.url)))
            .scalars()
            .all()
        )
        assert set(remaining) == {
            "https://example.com/referenced",
            "https://example.com/member",
        }


@pytest.mark.asyncio
async def test_get_urls_by_ids(factory: Factory) -> None:
    """Looking up URLs by primary key returns known rows and silently
    omits unknown ids.
    """
    async with factory.db_session.begin():
        store = factory.create_linkcheck_store()

        ids = await store.upsert_checked_urls(
            ["https://example.com/a", "https://example.com/b"]
        )
        unknown_id = max(ids.values()) + 1000

        records = await store.get_urls_by_ids(
            [ids["https://example.com/a"], unknown_id]
        )
        assert [(r.id, r.url) for r in records] == [
            (ids["https://example.com/a"], "https://example.com/a")
        ]

        assert await store.get_urls_by_ids([]) == []
