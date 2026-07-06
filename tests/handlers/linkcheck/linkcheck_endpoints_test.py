"""Tests for the /ook/linkcheck endpoints."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import structlog
from httpx import AsyncClient
from safir.database import create_async_session, create_database_engine
from sqlalchemy import select

from ook.config import config
from ook.dbschema.linkcheck import SqlCheckedUrl, SqlUrlOccurrence
from ook.domain.linkcheck import LinkState, LinkStatus, RetryLadderConfig
from ook.services.linkcheck import LinkCheckService, UrlChecker
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


async def _execute_check(
    check_id: int, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Execute a check against the test database with mocked HTTP, the
    way the Kafka consumer will invoke the service.
    """

    async def resolve_host(host: str) -> Sequence[str]:
        return ["93.184.216.34"]

    logger = structlog.get_logger("test")
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    session = await create_async_session(engine)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        checker = UrlChecker(
            http_client=http_client,
            logger=logger,
            request_timeout=timedelta(seconds=5),
            host_interval=timedelta(seconds=0),
            resolve_host=resolve_host,
        )
        service = LinkCheckService(
            linkcheck_store=LinkCheckStore(session=session, logger=logger),
            logger=logger,
            freshness_ttl=config.linkcheck_freshness_ttl,
            max_urls_per_check=config.linkcheck_max_urls_per_check,
            url_checker=checker,
            retry_ladder=RetryLadderConfig(),
            check_retention=config.linkcheck_check_retention,
        )
        async with session.begin():
            await service.execute_check(check_id)
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
async def test_poll_after_execution_reflects_results(
    client: AsyncClient,
) -> None:
    """After a submitted check is executed, polling it shows the check
    complete with per-URL results from the transition engine.
    """
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "ltd_slug": "sqr-000",
            "default_branch": True,
            "urls": [
                {"url": "https://example.com/ok", "paths": ["index"]},
                {"url": "https://example.com/gone", "paths": ["index"]},
            ],
        },
    )
    assert response.status_code == 202
    location = response.headers["Location"]
    check_id = int(location.rstrip("/").rsplit("/", 1)[-1])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gone":
            return httpx.Response(404)
        return httpx.Response(200)

    await _execute_check(check_id, handler)

    response = await client.get(location)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "complete"
    assert data["date_completed"] is not None
    assert data["summary"] == {
        "pending": 0,
        "ok": 1,
        "redirected": 0,
        "failing": 0,
        "broken": 1,
        "unsupported": 0,
    }
    results = {u["url"]: u for u in data["urls"]}
    ok = results["https://example.com/ok"]
    assert ok["status"] == "ok"
    assert ok["status_code"] == 200
    assert ok["checked_at"] is not None
    gone = results["https://example.com/gone"]
    assert gone["status"] == "broken"
    assert gone["status_code"] == 404
    assert gone["checked_at"] is not None


@pytest.mark.asyncio
async def test_get_unknown_check(client: AsyncClient) -> None:
    """Polling an unknown check returns 404."""
    response = await client.get("/ook/linkcheck/checks/123456789")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_unknown_url_record(client: AsyncClient) -> None:
    """Looking up a URL that has never been submitted returns 404."""
    response = await client.get(
        "/ook/linkcheck/urls",
        params={"url": "https://example.com/never-submitted"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_url_record(client: AsyncClient) -> None:
    """``GET /ook/linkcheck/urls?url=...`` returns the URL's stored
    record, including its check state and project-page occurrences. The
    lookup URL is canonicalized (fragments stripped).
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    await _seed_url_state(
        LinkState(
            url="https://example.com/moved",
            status=LinkStatus.redirected,
            checked_at=now,
            last_ok_at=now,
            status_code=200,
            redirect_status_code=301,
            redirect_url="https://example.com/new-location",
        )
    )

    # Establish occurrences via a default-branch submission.
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "ltd_slug": "sqr-000",
            "default_branch": True,
            "urls": [
                {
                    "url": "https://example.com/moved",
                    "paths": ["index", "guide"],
                },
            ],
        },
    )
    assert response.status_code == 202

    response = await client.get(
        "/ook/linkcheck/urls",
        params={"url": "https://example.com/moved#section"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["url"] == "https://example.com/moved"
    assert data["status"] == "redirected"
    assert data["status_code"] == 200
    assert data["redirect_status_code"] == 301
    assert data["redirect_url"] == "https://example.com/new-location"
    assert data["error"] is None
    assert data["last_checked_at"] is not None
    assert data["occurrences"] == [
        {"ltd_slug": "sqr-000", "path": "guide"},
        {"ltd_slug": "sqr-000", "path": "index"},
    ]


async def _seed_project_links(client: AsyncClient) -> None:
    """Seed three URL states and a default-branch submission that
    establishes their occurrences for project ``sqr-000``.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    await _seed_url_state(
        LinkState(
            url="https://example.com/a-ok",
            status=LinkStatus.ok,
            checked_at=now,
            last_ok_at=now,
            status_code=200,
        )
    )
    await _seed_url_state(
        LinkState(
            url="https://example.com/b-moved",
            status=LinkStatus.redirected,
            checked_at=now,
            last_ok_at=now,
            status_code=200,
            redirect_status_code=301,
            redirect_url="https://example.com/new-location",
        )
    )
    await _seed_url_state(
        LinkState(
            url="https://example.com/c-gone",
            status=LinkStatus.broken,
            checked_at=now,
            failing_since=now,
            failure_count=1,
            status_code=404,
            error="HTTP status 404",
        )
    )

    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "ltd_slug": "sqr-000",
            "default_branch": True,
            "urls": [
                {"url": "https://example.com/a-ok", "paths": ["index"]},
                {
                    "url": "https://example.com/b-moved",
                    "paths": ["index", "guide"],
                },
                {"url": "https://example.com/c-gone", "paths": ["changelog"]},
            ],
        },
    )
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_get_project_links(client: AsyncClient) -> None:
    """``GET /ook/linkcheck/projects/{slug}/links`` lists all of a
    project's links with their health states and page paths, ordered by
    URL.
    """
    await _seed_project_links(client)

    response = await client.get("/ook/linkcheck/projects/sqr-000/links")
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "3"
    data = response.json()
    assert [link["url"] for link in data] == [
        "https://example.com/a-ok",
        "https://example.com/b-moved",
        "https://example.com/c-gone",
    ]
    ok, moved, gone = data
    assert ok["status"] == "ok"
    assert ok["status_code"] == 200
    assert ok["paths"] == ["index"]
    assert moved["status"] == "redirected"
    assert moved["redirect_status_code"] == 301
    assert moved["redirect_url"] == "https://example.com/new-location"
    assert moved["paths"] == ["guide", "index"]
    assert gone["status"] == "broken"
    assert gone["status_code"] == 404
    assert gone["error"] == "HTTP status 404"
    assert gone["paths"] == ["changelog"]

    # A project with no recorded occurrences has no links.
    response = await client.get("/ook/linkcheck/projects/nothing/links")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_project_links_status_filter(client: AsyncClient) -> None:
    """``?status=redirected`` lists permanently-redirected links with
    their final locations, and ``?status=broken`` filters likewise.
    """
    await _seed_project_links(client)

    response = await client.get(
        "/ook/linkcheck/projects/sqr-000/links",
        params={"status": "redirected"},
    )
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "1"
    data = response.json()
    assert len(data) == 1
    assert data[0]["url"] == "https://example.com/b-moved"
    assert data[0]["status"] == "redirected"
    assert data[0]["redirect_url"] == "https://example.com/new-location"

    response = await client.get(
        "/ook/linkcheck/projects/sqr-000/links",
        params={"status": "broken"},
    )
    assert response.status_code == 200
    data = response.json()
    assert [link["url"] for link in data] == ["https://example.com/c-gone"]

    # An invalid status value is rejected.
    response = await client.get(
        "/ook/linkcheck/projects/sqr-000/links",
        params={"status": "bogus"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_project_links_pagination(client: AsyncClient) -> None:
    """The project links listing paginates with a keyset cursor:
    following the ``Link`` header's next relation walks all entries in
    URL order, and every page carries the total count.
    """
    await _seed_project_links(client)

    urls: list[str] = []
    next_url: str | None = "/ook/linkcheck/projects/sqr-000/links?limit=1"
    pages = 0
    while next_url is not None:
        response = await client.get(next_url)
        assert response.status_code == 200
        assert response.headers["X-Total-Count"] == "3"
        data = response.json()
        assert len(data) == 1
        urls.extend(link["url"] for link in data)
        pages += 1
        links = response.links
        next_url = str(links["next"]["url"]) if "next" in links else None

    assert pages == 3
    assert urls == [
        "https://example.com/a-ok",
        "https://example.com/b-moved",
        "https://example.com/c-gone",
    ]
