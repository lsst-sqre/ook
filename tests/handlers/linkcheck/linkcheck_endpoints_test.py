"""Tests for the /ook/linkcheck endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import structlog
from httpx import AsyncClient
from safir.database import create_async_session, create_database_engine
from sqlalchemy import select

from ook.config import config
from ook.dbschema.linkcheck import SqlCheckedUrl, SqlUrlOccurrence
from ook.domain.linkcheck import LinkState, LinkStatus
from ook.storage.linkcheckstore import LinkCheckStore


async def _seed_url_state(state: LinkState) -> None:
    """Write a URL's check state directly to the test database."""
    logger = structlog.get_logger("test")
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    session = await create_async_session(engine)
    store = LinkCheckStore(session=session, logger=logger)
    async with session.begin():
        await store.upsert_url_state(state)
    await session.close()
    await engine.dispose()


async def _get_occurrences(ltd_slug: str) -> set[tuple[str, str]]:
    """Get a project's occurrence set as (url, path) tuples."""
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    session = await create_async_session(engine)
    async with session.begin():
        rows = (
            await session.execute(
                select(SqlCheckedUrl.url, SqlUrlOccurrence.path)
                .join(
                    SqlCheckedUrl,
                    SqlCheckedUrl.id == SqlUrlOccurrence.checked_url_id,
                )
                .where(SqlUrlOccurrence.ltd_slug == ltd_slug)
            )
        ).all()
    await session.close()
    await engine.dispose()
    return {(row.url, row.path) for row in rows}


@pytest.mark.asyncio
async def test_post_check_accepted(client: AsyncClient) -> None:
    """``POST /ook/linkcheck/checks`` returns 202 with a Location header
    pointing at the created check.
    """
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "ltd_slug": "sqr-000",
            "default_branch": True,
            "urls": [
                {"url": "https://example.com/page", "paths": ["index"]},
            ],
        },
    )
    assert response.status_code == 202
    location = response.headers["Location"]
    assert "/ook/linkcheck/checks/" in location


@pytest.mark.asyncio
async def test_submit_and_poll_mixed_urls(client: AsyncClient) -> None:
    """Polling a submitted check shows immediate statuses for
    cached-fresh and unsupported URLs and pending for due URLs, with
    submitted URLs canonicalized (fragments stripped).
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    await _seed_url_state(
        LinkState(
            url="https://example.com/fresh",
            status=LinkStatus.ok,
            checked_at=now,
            last_ok_at=now,
            status_code=200,
        )
    )

    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "ltd_slug": "sqr-000",
            "default_branch": True,
            "urls": [
                # Fragment variant of the seeded fresh URL.
                {"url": "https://example.com/fresh#section", "paths": ["a"]},
                {"url": "https://example.com/unknown", "paths": ["a", "b"]},
                {"url": "mailto:someone@example.com", "paths": ["a"]},
            ],
        },
    )
    assert response.status_code == 202
    location = response.headers["Location"]

    response = await client.get(location)
    assert response.status_code == 200
    data = response.json()
    assert data["ltd_slug"] == "sqr-000"
    assert data["default_branch"] is True
    assert data["status"] == "pending"
    assert data["date_completed"] is None
    assert data["summary"] == {
        "pending": 1,
        "ok": 1,
        "redirected": 0,
        "failing": 0,
        "broken": 0,
        "unsupported": 1,
    }
    results = {u["url"]: u for u in data["urls"]}
    assert set(results) == {
        "https://example.com/fresh",
        "https://example.com/unknown",
        "mailto:someone@example.com",
    }
    fresh = results["https://example.com/fresh"]
    assert fresh["status"] == "ok"
    assert fresh["status_code"] == 200
    assert fresh["checked_at"] is not None
    unknown = results["https://example.com/unknown"]
    assert unknown["status"] == "pending"
    assert unknown["status_code"] is None
    assert unknown["checked_at"] is None
    unsupported = results["mailto:someone@example.com"]
    assert unsupported["status"] == "unsupported"
    assert data["self_url"].endswith(f"/ook/linkcheck/checks/{data['id']}")


@pytest.mark.asyncio
async def test_occurrences_replaced_only_for_default_branch(
    client: AsyncClient,
) -> None:
    """Only default-branch submissions replace the project's occurrence
    set; PR-build submissions leave it untouched but still receive full
    results.
    """
    # Default-branch submission establishes the occurrence set.
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "ltd_slug": "sqr-000",
            "default_branch": True,
            "urls": [
                {"url": "https://example.com/a#frag", "paths": ["index"]},
                {"url": "https://example.com/b", "paths": ["index", "guide"]},
            ],
        },
    )
    assert response.status_code == 202
    assert await _get_occurrences("sqr-000") == {
        ("https://example.com/a", "index"),
        ("https://example.com/b", "index"),
        ("https://example.com/b", "guide"),
    }

    # A PR-build submission does not touch the occurrence set...
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "ltd_slug": "sqr-000",
            "default_branch": False,
            "urls": [
                {"url": "https://example.com/c", "paths": ["newpage"]},
            ],
        },
    )
    assert response.status_code == 202
    pr_location = response.headers["Location"]
    assert await _get_occurrences("sqr-000") == {
        ("https://example.com/a", "index"),
        ("https://example.com/b", "index"),
        ("https://example.com/b", "guide"),
    }

    # ...but still receives full results.
    response = await client.get(pr_location)
    assert response.status_code == 200
    data = response.json()
    assert [u["url"] for u in data["urls"]] == ["https://example.com/c"]

    # A later default-branch submission replaces the occurrence set.
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "ltd_slug": "sqr-000",
            "default_branch": True,
            "urls": [
                {"url": "https://example.com/b", "paths": ["changelog"]},
            ],
        },
    )
    assert response.status_code == 202
    assert await _get_occurrences("sqr-000") == {
        ("https://example.com/b", "changelog"),
    }


@pytest.mark.asyncio
async def test_url_cap_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Submissions exceeding the URL cap are rejected with a clear
    error.
    """
    monkeypatch.setattr(config, "linkcheck_max_urls_per_check", 2)

    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "ltd_slug": "sqr-000",
            "default_branch": True,
            "urls": [
                {"url": f"https://example.com/{i}", "paths": ["index"]}
                for i in range(3)
            ],
        },
    )
    assert response.status_code == 422
    data = response.json()
    assert data["detail"][0]["type"] == "too_many_urls"
    assert "exceeds the per-check limit of 2" in data["detail"][0]["msg"]

    # Fragment variants collapse to one canonical URL, so an equivalent
    # submission under the cap is accepted.
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "ltd_slug": "sqr-000",
            "default_branch": True,
            "urls": [
                {"url": "https://example.com/a#one", "paths": ["index"]},
                {"url": "https://example.com/a#two", "paths": ["index"]},
                {"url": "https://example.com/b", "paths": ["index"]},
            ],
        },
    )
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_get_unknown_check(client: AsyncClient) -> None:
    """Polling an unknown check returns 404."""
    response = await client.get("/ook/linkcheck/checks/123456789")
    assert response.status_code == 404
