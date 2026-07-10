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
from ook.domain.base32id import serialize_ook_base32_id, validate_base32_id
from ook.domain.linkcheck import LinkState, LinkStatus, RetryLadderConfig
from ook.services.linkcheck import LinkCheckService, UrlChecker
from ook.storage.linkcheckstore import LinkCheckStore

ORIGIN = "https://sqr-000.lsst.io"
"""The origin base URL used for test submissions."""


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


async def _get_occurrences(origin_base_url: str) -> set[tuple[str, str]]:
    """Get an origin's occurrence set as (url, origin_path) tuples."""
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    session = await create_async_session(engine)
    async with session.begin():
        rows = (
            await session.execute(
                select(SqlCheckedUrl.url, SqlUrlOccurrence.origin_path)
                .join(
                    SqlCheckedUrl,
                    SqlCheckedUrl.id == SqlUrlOccurrence.checked_url_id,
                )
                .where(SqlUrlOccurrence.origin_base_url == origin_base_url)
            )
        ).all()
    await session.close()
    await engine.dispose()
    return {(row.url, row.origin_path) for row in rows}


@pytest.mark.asyncio
async def test_post_check_accepted(client: AsyncClient) -> None:
    """``POST /ook/linkcheck/checks`` with due URLs returns 202 with the
    pending check resource as the body and a Location header that
    matches a subsequent GET.
    """
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "origin_base_url": ORIGIN,
            "is_default_version": True,
            "urls": [
                {
                    "url": "https://example.com/page",
                    "origin_paths": ["index"],
                },
            ],
        },
    )
    assert response.status_code == 202
    location = response.headers["Location"]
    assert "/ook/linkcheck/checks/" in location

    data = response.json()
    assert data["status"] == "pending"
    assert data["origin_base_url"] == ORIGIN
    assert data["is_default_version"] is True
    assert data["self_url"] == location
    assert location.endswith(f"/ook/linkcheck/checks/{data['id']}")
    assert [u["url"] for u in data["urls"]] == ["https://example.com/page"]

    # The body matches a subsequent GET of the check on its stable
    # fields (execution may advance the status concurrently).
    get_response = await client.get(location)
    assert get_response.status_code == 200
    get_data = get_response.json()
    for field in ("id", "self_url", "origin_base_url", "is_default_version"):
        assert get_data[field] == data[field]
    assert [u["url"] for u in get_data["urls"]] == [
        u["url"] for u in data["urls"]
    ]


@pytest.mark.asyncio
async def test_check_id_is_base32(client: AsyncClient) -> None:
    """A check is exposed by a hyphenated Crockford base32 id (not the
    raw auto-increment PK), and that id round-trips through the Location
    header back to the same check.
    """
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "origin_base_url": ORIGIN,
            "is_default_version": True,
            "urls": [
                {"url": "https://example.com/page", "origin_paths": ["index"]},
            ],
        },
    )
    assert response.status_code == 202
    check_id = response.json()["id"]

    # The id is the hyphenated base32 string form, not a raw integer.
    assert isinstance(check_id, str)
    assert "-" in check_id
    # It decodes with a valid checksum and re-serializes to the same form.
    assert serialize_ook_base32_id(validate_base32_id(check_id)) == check_id

    # The Location targets that id and round-trips to the same check.
    location = response.headers["Location"]
    assert location.endswith(f"/ook/linkcheck/checks/{check_id}")
    get_response = await client.get(location)
    assert get_response.status_code == 200
    assert get_response.json()["id"] == check_id


@pytest.mark.asyncio
async def test_post_check_all_fresh_returns_complete(
    client: AsyncClient,
) -> None:
    """An all-fresh submission completes at submission and returns 200
    with the finished check as the body (no polling needed), still with
    a Location header.
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
            "origin_base_url": ORIGIN,
            "is_default_version": False,
            "urls": [
                {
                    "url": "https://example.com/fresh",
                    "origin_paths": ["index"],
                },
                {"url": "mailto:someone@example.com", "origin_paths": ["a"]},
            ],
        },
    )
    assert response.status_code == 200
    location = response.headers["Location"]
    data = response.json()
    assert data["status"] == "complete"
    assert data["date_completed"] is not None
    assert data["self_url"] == location
    results = {u["url"]: u for u in data["urls"]}
    assert results["https://example.com/fresh"]["status"] == "ok"
    assert results["mailto:someone@example.com"]["status"] == "unsupported"

    # No execution is enqueued, so the body is the final resource and
    # matches a subsequent GET exactly.
    get_response = await client.get(location)
    assert get_response.status_code == 200
    assert get_response.json() == data


@pytest.mark.asyncio
async def test_check_poll_origin_paths_all_fresh(client: AsyncClient) -> None:
    """A completed-at-submission check echoes each URL's submitted
    origin paths, sorted and de-duplicated across fragment variants, on
    the 200 fast path — sourced from the submission itself, not the
    origin's occurrence set (this is a non-default PR build).
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
            "origin_base_url": ORIGIN,
            "is_default_version": False,
            "urls": [
                # Fragment variants of one canonical URL, whose page
                # paths merge (and the duplicate collapses).
                {
                    "url": "https://example.com/fresh#a",
                    "origin_paths": ["guide"],
                },
                {
                    "url": "https://example.com/fresh#b",
                    "origin_paths": ["index", "guide"],
                },
            ],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "complete"
    results = {u["url"]: u for u in data["urls"]}
    assert results["https://example.com/fresh"]["origin_paths"] == [
        "guide",
        "index",
    ]

    # The PR build did not replace the origin's occurrence set, so the
    # paths can only have come from the per-check submission record.
    assert await _get_occurrences(ORIGIN) == set()

    # The poll GET carries the same origin paths.
    get_response = await client.get(response.headers["Location"])
    assert get_response.json() == data


@pytest.mark.asyncio
async def test_check_poll_origin_paths_after_execution(
    client: AsyncClient,
) -> None:
    """After execution, polling a check still reports each URL's
    submitted origin paths alongside its resolved status.
    """
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "origin_base_url": ORIGIN,
            "is_default_version": False,
            "urls": [
                {
                    "url": "https://example.com/ok",
                    "origin_paths": ["index", "guide"],
                },
                {
                    "url": "https://example.com/gone",
                    "origin_paths": ["changelog"],
                },
            ],
        },
    )
    assert response.status_code == 202
    location = response.headers["Location"]
    check_id = validate_base32_id(location.rstrip("/").rsplit("/", 1)[-1])

    # Pending URLs already carry their submitted paths before execution.
    pending = {
        u["url"]: u for u in (await client.get(location)).json()["urls"]
    }
    assert pending["https://example.com/ok"]["origin_paths"] == [
        "guide",
        "index",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gone":
            return httpx.Response(404)
        return httpx.Response(200)

    await _execute_check(check_id, handler)

    response = await client.get(location)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "complete"
    results = {u["url"]: u for u in data["urls"]}
    assert results["https://example.com/ok"]["status"] == "ok"
    assert results["https://example.com/ok"]["origin_paths"] == [
        "guide",
        "index",
    ]
    assert results["https://example.com/gone"]["status"] == "broken"
    assert results["https://example.com/gone"]["origin_paths"] == ["changelog"]


@pytest.mark.asyncio
async def test_post_check_origin_normalization(client: AsyncClient) -> None:
    """Origin base URLs are normalized (host lowercased, trailing slash
    stripped) so equivalent spellings map to one origin, and invalid
    origins are rejected with 422.
    """
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "origin_base_url": "HTTPS://SQR-000.LSST.IO/",
            "is_default_version": True,
            "urls": [
                {"url": "https://example.com/a", "origin_paths": ["index"]},
            ],
        },
    )
    assert response.status_code == 202
    assert response.json()["origin_base_url"] == ORIGIN
    assert await _get_occurrences(ORIGIN) == {
        ("https://example.com/a", "index"),
    }

    for bad_origin in (
        "sqr-000.lsst.io",  # missing scheme
        "ftp://sqr-000.lsst.io",  # unsupported scheme
        "https://sqr-000.lsst.io?branch=main",  # query forbidden
        "https://sqr-000.lsst.io#section",  # fragment forbidden
        "https://",  # missing host
    ):
        response = await client.post(
            "/ook/linkcheck/checks",
            json={
                "origin_base_url": bad_origin,
                "is_default_version": True,
                "urls": [
                    {
                        "url": "https://example.com/a",
                        "origin_paths": ["index"],
                    },
                ],
            },
        )
        assert response.status_code == 422, bad_origin


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
            "origin_base_url": ORIGIN,
            "is_default_version": True,
            "urls": [
                # Fragment variant of the seeded fresh URL.
                {
                    "url": "https://example.com/fresh#section",
                    "origin_paths": ["a"],
                },
                {
                    "url": "https://example.com/unknown",
                    "origin_paths": ["a", "b"],
                },
                {"url": "mailto:someone@example.com", "origin_paths": ["a"]},
            ],
        },
    )
    assert response.status_code == 202
    location = response.headers["Location"]

    response = await client.get(location)
    assert response.status_code == 200
    data = response.json()
    assert data["origin_base_url"] == ORIGIN
    assert data["is_default_version"] is True
    assert data["status"] == "pending"
    assert data["date_completed"] is None
    assert data["summary"] == {
        "pending": 1,
        "ok": 1,
        "redirected": 0,
        "failing": 0,
        "broken": 0,
        "blocked": 0,
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
async def test_occurrences_replaced_only_for_default_version(
    client: AsyncClient,
) -> None:
    """Only default-version submissions replace the origin's occurrence
    set; PR-build submissions leave it untouched but still receive full
    results.
    """
    # Default-version submission establishes the occurrence set.
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "origin_base_url": ORIGIN,
            "is_default_version": True,
            "urls": [
                {
                    "url": "https://example.com/a#frag",
                    "origin_paths": ["index"],
                },
                {
                    "url": "https://example.com/b",
                    "origin_paths": ["index", "guide"],
                },
            ],
        },
    )
    assert response.status_code == 202
    assert await _get_occurrences(ORIGIN) == {
        ("https://example.com/a", "index"),
        ("https://example.com/b", "index"),
        ("https://example.com/b", "guide"),
    }

    # A PR-build submission does not touch the occurrence set...
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "origin_base_url": ORIGIN,
            "is_default_version": False,
            "urls": [
                {"url": "https://example.com/c", "origin_paths": ["newpage"]},
            ],
        },
    )
    assert response.status_code == 202
    pr_location = response.headers["Location"]
    assert await _get_occurrences(ORIGIN) == {
        ("https://example.com/a", "index"),
        ("https://example.com/b", "index"),
        ("https://example.com/b", "guide"),
    }

    # ...but still receives full results.
    response = await client.get(pr_location)
    assert response.status_code == 200
    data = response.json()
    assert [u["url"] for u in data["urls"]] == ["https://example.com/c"]

    # A later default-version submission replaces the occurrence set.
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "origin_base_url": ORIGIN,
            "is_default_version": True,
            "urls": [
                {
                    "url": "https://example.com/b",
                    "origin_paths": ["changelog"],
                },
            ],
        },
    )
    assert response.status_code == 202
    assert await _get_occurrences(ORIGIN) == {
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
            "origin_base_url": ORIGIN,
            "is_default_version": True,
            "urls": [
                {"url": f"https://example.com/{i}", "origin_paths": ["index"]}
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
            "origin_base_url": ORIGIN,
            "is_default_version": True,
            "urls": [
                {
                    "url": "https://example.com/a#one",
                    "origin_paths": ["index"],
                },
                {
                    "url": "https://example.com/a#two",
                    "origin_paths": ["index"],
                },
                {"url": "https://example.com/b", "origin_paths": ["index"]},
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
            "origin_base_url": ORIGIN,
            "is_default_version": True,
            "urls": [
                {"url": "https://example.com/ok", "origin_paths": ["index"]},
                {"url": "https://example.com/gone", "origin_paths": ["index"]},
            ],
        },
    )
    assert response.status_code == 202
    location = response.headers["Location"]
    check_id = validate_base32_id(location.rstrip("/").rsplit("/", 1)[-1])

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
        "blocked": 0,
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
async def test_poll_reports_bot_blocked_url_as_blocked(
    client: AsyncClient,
) -> None:
    """A bot-blocked URL is reported with the ``blocked`` status and
    counted in the summary's ``blocked`` bucket, excluded from failing
    and broken, with the diagnostic error detail retained.
    """
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "origin_base_url": ORIGIN,
            "is_default_version": True,
            "urls": [
                {"url": "https://example.com/ok", "origin_paths": ["index"]},
                {
                    "url": "https://example.com/guarded",
                    "origin_paths": ["index"],
                },
            ],
        },
    )
    assert response.status_code == 202
    location = response.headers["Location"]
    check_id = validate_base32_id(location.rstrip("/").rsplit("/", 1)[-1])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/guarded":
            return httpx.Response(
                403,
                headers={"server": "cloudflare", "cf-mitigated": "block"},
            )
        return httpx.Response(200)

    await _execute_check(check_id, handler)

    response = await client.get(location)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "complete"
    assert data["summary"] == {
        "pending": 0,
        "ok": 1,
        "redirected": 0,
        "failing": 0,
        "broken": 0,
        "blocked": 1,
        "unsupported": 0,
    }
    results = {u["url"]: u for u in data["urls"]}
    guarded = results["https://example.com/guarded"]
    assert guarded["status"] == "blocked"
    assert guarded["status_code"] == 403
    assert "bot protection" in guarded["error"]


@pytest.mark.asyncio
async def test_get_unknown_check(client: AsyncClient) -> None:
    """Polling a well-formed but unknown check id returns 404, while a
    malformed base32 id is rejected with 422.
    """
    unknown_id = serialize_ook_base32_id(123456789)
    response = await client.get(f"/ook/linkcheck/checks/{unknown_id}")
    assert response.status_code == 404

    # A malformed id (bad checksum) fails path validation before the
    # lookup.
    response = await client.get("/ook/linkcheck/checks/not-a-valid-id")
    assert response.status_code == 422


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
    record, including its check state and origin-page occurrences. The
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

    # Establish occurrences via a default-version submission.
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "origin_base_url": ORIGIN,
            "is_default_version": True,
            "urls": [
                {
                    "url": "https://example.com/moved",
                    "origin_paths": ["index", "guide"],
                },
            ],
        },
    )
    assert response.status_code == 200

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
        {"origin_base_url": ORIGIN, "origin_path": "guide"},
        {"origin_base_url": ORIGIN, "origin_path": "index"},
    ]


async def _seed_origin_links(client: AsyncClient) -> None:
    """Seed three URL states and a default-version submission that
    establishes their occurrences for the test origin.
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
            "origin_base_url": ORIGIN,
            "is_default_version": True,
            "urls": [
                {"url": "https://example.com/a-ok", "origin_paths": ["index"]},
                {
                    "url": "https://example.com/b-moved",
                    "origin_paths": ["index", "guide"],
                },
                {
                    "url": "https://example.com/c-gone",
                    "origin_paths": ["changelog"],
                },
            ],
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_origin_links(client: AsyncClient) -> None:
    """``GET /ook/linkcheck/links?origin=...`` lists all of an origin's
    links with their health states and page paths, ordered by URL. The
    origin is normalized before the lookup.
    """
    await _seed_origin_links(client)

    response = await client.get(
        "/ook/linkcheck/links", params={"origin": ORIGIN}
    )
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
    assert ok["origin_paths"] == ["index"]
    assert moved["status"] == "redirected"
    assert moved["redirect_status_code"] == 301
    assert moved["redirect_url"] == "https://example.com/new-location"
    assert moved["origin_paths"] == ["guide", "index"]
    assert gone["status"] == "broken"
    assert gone["status_code"] == 404
    assert gone["error"] == "HTTP status 404"
    assert gone["origin_paths"] == ["changelog"]

    # An equivalent origin spelling normalizes to the same origin.
    response = await client.get(
        "/ook/linkcheck/links",
        params={"origin": "HTTPS://SQR-000.LSST.IO/"},
    )
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "3"

    # An origin with no recorded occurrences has no links.
    response = await client.get(
        "/ook/linkcheck/links",
        params={"origin": "https://nothing.lsst.io"},
    )
    assert response.status_code == 200
    assert response.json() == []

    # An invalid origin is rejected.
    response = await client.get(
        "/ook/linkcheck/links", params={"origin": "sqr-000.lsst.io"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_origin_links_status_filter(client: AsyncClient) -> None:
    """``?status=redirected`` lists permanently-redirected links with
    their final locations, and ``?status=broken`` filters likewise.
    """
    await _seed_origin_links(client)

    response = await client.get(
        "/ook/linkcheck/links",
        params={"origin": ORIGIN, "status": "redirected"},
    )
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "1"
    data = response.json()
    assert len(data) == 1
    assert data[0]["url"] == "https://example.com/b-moved"
    assert data[0]["status"] == "redirected"
    assert data[0]["redirect_url"] == "https://example.com/new-location"

    response = await client.get(
        "/ook/linkcheck/links",
        params={"origin": ORIGIN, "status": "broken"},
    )
    assert response.status_code == 200
    data = response.json()
    assert [link["url"] for link in data] == ["https://example.com/c-gone"]

    # An invalid status value is rejected.
    response = await client.get(
        "/ook/linkcheck/links",
        params={"origin": ORIGIN, "status": "bogus"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_origin_links_path_filter(client: AsyncClient) -> None:
    """``?path=`` narrows the listing to links occurring on one page
    while each returned link keeps its full ``origin_paths`` list, and
    composes with ``?status=``.
    """
    await _seed_origin_links(client)

    # ``?path=index`` returns only links occurring on the ``index``
    # page (a-ok and b-moved), each still carrying every page it occurs
    # on.
    response = await client.get(
        "/ook/linkcheck/links",
        params={"origin": ORIGIN, "path": "index"},
    )
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "2"
    data = response.json()
    assert [link["url"] for link in data] == [
        "https://example.com/a-ok",
        "https://example.com/b-moved",
    ]
    assert data[0]["origin_paths"] == ["index"]
    # b-moved occurs on both pages; the full list is preserved under
    # the filter.
    assert data[1]["origin_paths"] == ["guide", "index"]

    # ``?path=guide`` returns only b-moved.
    response = await client.get(
        "/ook/linkcheck/links",
        params={"origin": ORIGIN, "path": "guide"},
    )
    assert response.status_code == 200
    assert [link["url"] for link in response.json()] == [
        "https://example.com/b-moved",
    ]

    # ``?path=`` composes with ``?status=`` (AND): only b-moved is both
    # on ``index`` and ``redirected``; a-ok is on ``index`` but ``ok``.
    response = await client.get(
        "/ook/linkcheck/links",
        params={"origin": ORIGIN, "path": "index", "status": "redirected"},
    )
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "1"
    data = response.json()
    assert [link["url"] for link in data] == ["https://example.com/b-moved"]
    assert data[0]["origin_paths"] == ["guide", "index"]

    # A path matching no occurrences yields an empty list.
    response = await client.get(
        "/ook/linkcheck/links",
        params={"origin": ORIGIN, "path": "nonexistent"},
    )
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "0"
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_origin_links_pagination(client: AsyncClient) -> None:
    """The origin links listing paginates with a keyset cursor:
    following the ``Link`` header's next relation walks all entries in
    URL order, and every page carries the total count.
    """
    await _seed_origin_links(client)

    urls: list[str] = []
    next_url: str | None = f"/ook/linkcheck/links?origin={ORIGIN}&limit=1"
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
