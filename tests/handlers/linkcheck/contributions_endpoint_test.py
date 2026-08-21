"""Tests for the /ook/linkcheck contributions endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import respx
import structlog
from httpx import AsyncClient, Response
from safir.database import create_async_session, create_database_engine
from structlog.testing import capture_logs

from ook.config import config
from ook.domain.base32id import serialize_ook_base32_id, validate_base32_id
from ook.domain.linkcheck import LinkContribution, LinkState, LinkStatus
from ook.storage.linkcheckstore import LinkCheckStore
from tests.support.githuboidc import GitHubOidcSigningKey, JwksMock

ORIGIN = "https://sqr-000.lsst.io"
"""The origin base URL used for test submissions."""

REPOSITORY = "lsst-sqre/documenteer"
"""The repository the test tokens attest to."""

RUN_ID = "42"
"""The workflow run the test tokens attest to."""

WORKFLOW_REF = (
    "lsst-sqre/documenteer/.github/workflows/linkcheck.yaml@refs/heads/main"
)
"""The workflow the test tokens attest to."""


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


async def _get_contributions(check_id: int) -> list[LinkContribution]:
    """Read back the contributions recorded against a check."""
    logger = structlog.get_logger("test")
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    session = await create_async_session(engine)
    store = LinkCheckStore(session=session, logger=logger)
    async with session.begin():
        contributions = await store.get_contributions(check_id)
    await session.close()
    await engine.dispose()
    return contributions


async def _seed_blocked_url(url: str, **overrides: Any) -> None:
    """Seed a bot-blocked URL state, as a server check would leave it.

    The state is fresh and its blocked recheck is still in the future, so a
    check submitted for the URL resolves at submission rather than being
    executed in the background.
    """
    now = datetime.now(tz=UTC).replace(microsecond=0)
    await _seed_url_state(
        LinkState(
            url=url,
            status=LinkStatus.blocked,
            date_checked=now,
            consecutive_blocked_count=1,
            status_code=403,
            error="HTTP 403 (likely blocked by bot protection)",
            date_next_check=now + timedelta(hours=1),
            **overrides,
        )
    )


async def _seed_ok_url(url: str) -> None:
    """Seed a URL that Ook resolved from its own vantage point."""
    now = datetime.now(tz=UTC).replace(microsecond=0)
    await _seed_url_state(
        LinkState(
            url=url,
            status=LinkStatus.ok,
            date_checked=now,
            date_last_ok=now,
            status_code=200,
        )
    )


async def _submit_check(
    client: AsyncClient, urls: list[str]
) -> tuple[str, str]:
    """Submit a check for URLs whose states are already resolved.

    Returns
    -------
    tuple
        The check's base32 id and the URL it is polled at.
    """
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "origin_base_url": ORIGIN,
            "is_default_version": True,
            "urls": [{"url": url, "origin_paths": ["index"]} for url in urls],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"], response.headers["Location"]


def _result(url: str, **fields: Any) -> dict[str, Any]:
    """Build one contributed per-URL result body."""
    date_checked = datetime.now(tz=UTC) - timedelta(minutes=1)
    return {"url": url, "date_checked": date_checked.isoformat(), **fields}


def _body(
    key: GitHubOidcSigningKey,
    results: list[dict[str, Any]],
    **mint_kwargs: Any,
) -> dict[str, Any]:
    """Build a contribution request body signed by ``key``."""
    kwargs: dict[str, Any] = {
        "audience": config.oidc_audience,
        "repository": REPOSITORY,
        "run_id": RUN_ID,
        "workflow_ref": WORKFLOW_REF,
    }
    kwargs.update(mint_kwargs)
    return {
        "id_token": key.mint(**kwargs),
        "environment": {
            "provider": "github_actions",
            "repository": REPOSITORY,
            "run_url": (
                f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}"
            ),
            "checker_version": "documenteer 2.1.0",
        },
        "results": results,
    }


@pytest.mark.asyncio
async def test_contribution_resolves_blocked_url(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A contributed success for a blocked member URL runs through the
    status engine, and a subsequent poll of the check reports the URL ok
    with the contributing repository as its provenance.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    url = "https://example.com/guarded"
    await _seed_blocked_url(url)
    check_id, location = await _submit_check(client, [url])

    response = await client.post(
        f"/ook/linkcheck/checks/{check_id}/contributions",
        json=_body(key, [_result(url, status_code=200)]),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["check_id"] == check_id
    assert data["provenance"]["provider"] == "github_actions"
    assert data["provenance"]["repository"] == REPOSITORY
    assert data["provenance"]["run_id"] == RUN_ID
    assert data["provenance"]["workflow_ref"] == WORKFLOW_REF
    assert data["accepted"] == [{"url": url, "status": "ok"}]
    assert data["rejected"] == []

    poll = await client.get(location)
    assert poll.status_code == 200
    results = {entry["url"]: entry for entry in poll.json()["urls"]}
    assert results[url]["status"] == "ok"
    assert results[url]["status_code"] == 200
    assert results[url]["error"] is None
    assert results[url]["result_source"] == "contribution"
    assert results[url]["contributed_by"] == REPOSITORY


@pytest.mark.asyncio
async def test_contributed_permanent_redirect(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A contributed success reached through a permanent redirect resolves
    the URL to ``redirected`` and carries the redirect metadata, so the
    source can be updated to the new location.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    url = "https://example.com/moved"
    await _seed_blocked_url(url)
    check_id, location = await _submit_check(client, [url])

    response = await client.post(
        f"/ook/linkcheck/checks/{check_id}/contributions",
        json=_body(
            key,
            [
                _result(
                    url,
                    status_code=200,
                    redirect_status_code=301,
                    redirect_url="https://example.com/new-location",
                )
            ],
        ),
    )

    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == [
        {"url": url, "status": "redirected"}
    ]

    poll = await client.get(location)
    result = {entry["url"]: entry for entry in poll.json()["urls"]}[url]
    assert result["status"] == "redirected"
    assert result["redirect_status_code"] == 301
    assert result["redirect_url"] == "https://example.com/new-location"
    assert result["result_source"] == "contribution"


@pytest.mark.asyncio
async def test_contributed_failure_advances_the_ladder(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A contributed 404 is a confirmed failure: it extends the URL's
    failing streak on the retry ladder exactly as a server check would.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    url = "https://example.com/gone"
    an_hour_ago = datetime.now(tz=UTC).replace(microsecond=0) - timedelta(
        hours=1
    )
    await _seed_blocked_url(
        url,
        date_last_ok=an_hour_ago,
        date_failing_since=an_hour_ago,
        failure_count=1,
    )
    check_id, _ = await _submit_check(client, [url])

    response = await client.post(
        f"/ook/linkcheck/checks/{check_id}/contributions",
        json=_body(key, [_result(url, status_code=404)]),
    )

    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == [{"url": url, "status": "failing"}]

    record = await client.get("/ook/linkcheck/urls", params={"url": url})
    assert record.status_code == 200
    data = record.json()
    assert data["status"] == "failing"
    assert data["status_code"] == 404
    assert data["failure_count"] == 2
    assert data["date_failing_since"] is not None
    assert data["date_next_check"] is not None
    assert data["result_source"] == "contribution"
    assert data["contributed_by"] == REPOSITORY


@pytest.mark.asyncio
async def test_contributed_403_leaves_the_url_blocked(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A contributed 403 is inconclusive, not a confirmed failure: the
    client was blocked in turn, so the URL stays ``blocked`` rather than
    entering the failing-to-broken ladder.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    url = "https://example.com/still-guarded"
    await _seed_blocked_url(url)
    check_id, location = await _submit_check(client, [url])

    response = await client.post(
        f"/ook/linkcheck/checks/{check_id}/contributions",
        json=_body(key, [_result(url, status_code=403)]),
    )

    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == [{"url": url, "status": "blocked"}]

    poll = await client.get(location)
    result = {entry["url"]: entry for entry in poll.json()["urls"]}[url]
    assert result["status"] == "blocked"
    assert result["status_code"] == 403
    assert result["result_source"] == "contribution"

    record = (
        await client.get("/ook/linkcheck/urls", params={"url": url})
    ).json()
    # The block never advances the failing-to-broken ladder.
    assert record["failure_count"] == 0
    assert record["date_failing_since"] is None


@pytest.mark.asyncio
async def test_invalid_token_applies_nothing(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A contribution whose id-token fails verification is rejected as a
    whole, located at the token field, with nothing applied.
    """
    published = GitHubOidcSigningKey()
    impostor = GitHubOidcSigningKey(published.kid, key_name="impostor")
    JwksMock(respx_mock, [published])
    url = "https://example.com/guarded"
    await _seed_blocked_url(url)
    check_id, location = await _submit_check(client, [url])

    response = await client.post(
        f"/ook/linkcheck/checks/{check_id}/contributions",
        json=_body(impostor, [_result(url, status_code=200)]),
    )

    assert response.status_code == 422
    detail = response.json()["detail"][0]
    assert detail["type"] == "invalid_oidc_token"
    assert detail["loc"] == ["body", "id_token"]

    poll = await client.get(location)
    result = {entry["url"]: entry for entry in poll.json()["urls"]}[url]
    assert result["status"] == "blocked"
    assert result["result_source"] == "server"
    assert result["contributed_by"] is None


@pytest.mark.asyncio
async def test_missing_token_is_rejected(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """The id-token is required: a contribution without one never reaches
    the status engine.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    url = "https://example.com/guarded"
    await _seed_blocked_url(url)
    check_id, location = await _submit_check(client, [url])
    body = _body(key, [_result(url, status_code=200)])
    del body["id_token"]

    response = await client.post(
        f"/ook/linkcheck/checks/{check_id}/contributions", json=body
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "id_token"]

    poll = await client.get(location)
    result = {entry["url"]: entry for entry in poll.json()["urls"]}[url]
    assert result["status"] == "blocked"
    assert result["result_source"] == "server"


@pytest.mark.asyncio
async def test_contribution_to_unknown_check(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """Contributing against a check that does not exist is a 404."""
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    unknown_id = serialize_ook_base32_id(123456789)

    response = await client.post(
        f"/ook/linkcheck/checks/{unknown_id}/contributions",
        json=_body(
            key, [_result("https://example.com/guarded", status_code=200)]
        ),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_mixed_batch_partially_applies(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A batch mixing eligible and ineligible entries applies the eligible
    ones and reports the rest individually with their reasons.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    blocked = "https://example.com/guarded"
    resolved = "https://example.com/resolved"
    stranger = "https://example.com/never-submitted"
    unsupported = "mailto:someone@example.com"
    await _seed_blocked_url(blocked)
    await _seed_ok_url(resolved)
    check_id, location = await _submit_check(client, [blocked, resolved])

    response = await client.post(
        f"/ook/linkcheck/checks/{check_id}/contributions",
        json=_body(
            key,
            [
                _result(blocked, status_code=200),
                _result(resolved, status_code=200),
                _result(stranger, status_code=200),
                _result(unsupported, status_code=200),
                _result(blocked, status_code=404),
            ],
        ),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["accepted"] == [{"url": blocked, "status": "ok"}]
    assert [(entry["url"], entry["reason"]) for entry in data["rejected"]] == [
        (resolved, "not_blocked"),
        (stranger, "not_a_member"),
        (unsupported, "unsupported_url"),
        (blocked, "duplicate"),
    ]
    assert all(entry["message"] for entry in data["rejected"])

    poll = await client.get(location)
    results = {entry["url"]: entry for entry in poll.json()["urls"]}
    # The eligible entry applied...
    assert results[blocked]["status"] == "ok"
    assert results[blocked]["result_source"] == "contribution"
    # ...and the duplicate's 404 never ran, so the URL is not failing.
    assert results[blocked]["status_code"] == 200
    # The already-resolved URL keeps Ook's own result.
    assert results[resolved]["status"] == "ok"
    assert results[resolved]["result_source"] == "server"
    # A non-member URL is not created as a side effect of contributing.
    assert (
        await client.get("/ook/linkcheck/urls", params={"url": stranger})
    ).status_code == 404


@pytest.mark.asyncio
async def test_contribution_logs_repository_and_counts(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """Applying a batch logs the contributing repository with the accepted
    and rejected counts, so operators can see what CI is contributing.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    blocked = "https://example.com/guarded"
    stranger = "https://example.com/never-submitted"
    await _seed_blocked_url(blocked)
    check_id, _ = await _submit_check(client, [blocked])

    with capture_logs() as captured:
        response = await client.post(
            f"/ook/linkcheck/checks/{check_id}/contributions",
            json=_body(
                key,
                [
                    _result(blocked, status_code=200),
                    _result(stranger, status_code=200),
                ],
            ),
        )

    assert response.status_code == 200, response.text
    applied = [
        event
        for event in captured
        if event["event"] == "Applied link-check contributions"
    ]
    assert len(applied) == 1
    assert applied[0]["repository"] == REPOSITORY
    assert applied[0]["run_id"] == RUN_ID
    assert applied[0]["accepted_count"] == 1
    assert applied[0]["rejected_count"] == 1
    rejected = [
        event
        for event in captured
        if event["event"] == "Rejected link-check contributions"
    ]
    assert len(rejected) == 1
    assert rejected[0]["repository"] == REPOSITORY
    assert rejected[0]["reasons"] == {"not_a_member": 1}


@pytest.mark.asyncio
async def test_contributed_result_ages_out_with_the_freshness_ttl(
    client: AsyncClient,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A contributed result is fresh on the shared freshness TTL exactly as
    a server check is: a later submission reuses it while it is fresh and
    treats the URL as due once the TTL lapses.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    # A background execution of the re-submitted check would fetch the URL,
    # so mock it rather than leaving the request unrouted.
    respx_mock.route(host="example.com").mock(return_value=Response(200))
    url = "https://example.com/guarded"
    await _seed_blocked_url(url)
    check_id, _ = await _submit_check(client, [url])

    response = await client.post(
        f"/ook/linkcheck/checks/{check_id}/contributions",
        json=_body(key, [_result(url, status_code=200)]),
    )
    assert response.status_code == 200, response.text

    # Within the TTL the contributed result is fresh, so a new submission
    # resolves at submission (200) and reports it.
    _, location = await _submit_check(client, [url])
    results = {
        entry["url"]: entry
        for entry in (await client.get(location)).json()["urls"]
    }
    assert results[url]["status"] == "ok"
    assert results[url]["result_source"] == "contribution"

    # Once the TTL lapses the URL is due again, so the same submission is
    # accepted for execution (202) with the URL pending rather than
    # reported from the contributed result.
    monkeypatch.setattr(config, "linkcheck_freshness_ttl", timedelta(0))
    response = await client.post(
        "/ook/linkcheck/checks",
        json={
            "origin_base_url": ORIGIN,
            "is_default_version": False,
            "urls": [{"url": url, "origin_paths": ["index"]}],
        },
    )
    assert response.status_code == 202
    assert response.json()["urls"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_contribution_is_recorded_with_its_provenance(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """Each applied result is persisted as a contribution row carrying the
    attested provenance and the client's own advisory check time, so a
    check's contributed history outlives the URL state it produced.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    url = "https://example.com/guarded"
    await _seed_blocked_url(url)
    check_id, _ = await _submit_check(client, [url])
    result = _result(url, status_code=200)

    response = await client.post(
        f"/ook/linkcheck/checks/{check_id}/contributions",
        json=_body(key, [result]),
    )
    assert response.status_code == 200, response.text

    contributions = await _get_contributions(validate_base32_id(check_id))
    assert len(contributions) == 1
    stored = contributions[0]
    assert stored.result.url == url
    assert stored.result.status_code == 200
    assert stored.result.date_checked == datetime.fromisoformat(
        result["date_checked"]
    )
    assert stored.provenance.repository == REPOSITORY
    assert stored.provenance.run_id == RUN_ID
    assert stored.provenance.workflow_ref == WORKFLOW_REF
    assert stored.provenance.run_url == (
        f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}"
    )
    assert stored.provenance.checker_version == "documenteer 2.1.0"
    assert stored.date_received is not None


@pytest.mark.asyncio
async def test_run_url_round_trips_as_a_string(
    client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """The run URL is parsed as a URL on the way in but stays text
    everywhere after: it comes back from the response and the contribution
    row as a normalized string.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    url = "https://example.com/guarded"
    await _seed_blocked_url(url)
    check_id, _ = await _submit_check(client, [url])
    body = _body(key, [_result(url, status_code=200)])
    # A bare host, which parsing normalizes with a trailing slash.
    body["environment"]["run_url"] = "https://ci.example.com"

    response = await client.post(
        f"/ook/linkcheck/checks/{check_id}/contributions", json=body
    )

    assert response.status_code == 200, response.text
    assert response.json()["provenance"]["run_url"] == (
        "https://ci.example.com/"
    )

    contributions = await _get_contributions(validate_base32_id(check_id))
    assert contributions[0].provenance.run_url == "https://ci.example.com/"


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme_url", ["javascript:alert(1)", "data:,x"])
async def test_non_http_run_url_applies_nothing(
    client: AsyncClient,
    respx_mock: respx.Router,
    scheme_url: str,
) -> None:
    """The advisory run URL is only ever displayed, so it must be a URL a
    report can safely link: a non-http(s) scheme is rejected at parse time
    with nothing applied.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    url = "https://example.com/guarded"
    await _seed_blocked_url(url)
    check_id, location = await _submit_check(client, [url])
    body = _body(key, [_result(url, status_code=200)])
    body["environment"]["run_url"] = scheme_url

    response = await client.post(
        f"/ook/linkcheck/checks/{check_id}/contributions", json=body
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == [
        "body",
        "environment",
        "run_url",
    ]

    poll = await client.get(location)
    result = {entry["url"]: entry for entry in poll.json()["urls"]}[url]
    assert result["status"] == "blocked"
    assert result["result_source"] == "server"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_url", "https://example.com/" + "x" * 4000),
        ("checker_version", "v" * 4000),
        ("repository", "lsst-sqre/" + "r" * 4000),
    ],
)
async def test_over_length_environment_field_applies_nothing(
    client: AsyncClient,
    respx_mock: respx.Router,
    field: str,
    value: str,
) -> None:
    """The environment block's text fields are persisted on every
    contribution row, so each is capped: an over-length value is rejected at
    parse time rather than written.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    url = "https://example.com/guarded"
    await _seed_blocked_url(url)
    check_id, location = await _submit_check(client, [url])
    body = _body(key, [_result(url, status_code=200)])
    body["environment"][field] = value

    response = await client.post(
        f"/ook/linkcheck/checks/{check_id}/contributions", json=body
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == [
        "body",
        "environment",
        field,
    ]

    poll = await client.get(location)
    result = {entry["url"]: entry for entry in poll.json()["urls"]}[url]
    assert result["result_source"] == "server"


@pytest.mark.asyncio
async def test_result_cap_rejected(
    client: AsyncClient,
    respx_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A contributed batch is capped by the same per-check URL limit as a
    check submission, so the two write endpoints agree on how much a client
    may send at once.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    urls = [f"https://example.com/guarded-{i}" for i in range(3)]
    for url in urls:
        await _seed_blocked_url(url)
    check_id, location = await _submit_check(client, urls)
    # Lowered only after the check exists, since the submission endpoint
    # enforces the same cap.
    monkeypatch.setattr(config, "linkcheck_max_urls_per_check", 2)

    response = await client.post(
        f"/ook/linkcheck/checks/{check_id}/contributions",
        json=_body(key, [_result(url, status_code=200) for url in urls]),
    )

    assert response.status_code == 422
    detail = response.json()["detail"][0]
    assert detail["type"] == "too_many_urls"
    assert "exceeds the per-check limit of 2" in detail["msg"]
    assert detail["loc"] == ["body", "results"]

    poll = await client.get(location)
    results = {entry["url"]: entry for entry in poll.json()["urls"]}
    assert all(results[url]["result_source"] == "server" for url in urls), (
        "an over-cap batch must apply nothing"
    )

    # A batch at the cap is accepted.
    response = await client.post(
        f"/ook/linkcheck/checks/{check_id}/contributions",
        json=_body(key, [_result(url, status_code=200) for url in urls[:2]]),
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["accepted"]) == 2
