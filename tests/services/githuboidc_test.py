"""Tests for the GitHubOidcVerifier."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import respx
import structlog
from httpx import AsyncClient

from ook.config import config
from ook.exceptions import GitHubOidcUnavailableError, InvalidOidcTokenError
from ook.factory import Factory
from ook.services.githuboidc import GitHubOidcVerifier

from ..support.githuboidc import TEST_AUDIENCE, GitHubOidcSigningKey, JwksMock

_TINY_TTL = timedelta(milliseconds=1)
"""A JWKS TTL short enough that a brief sleep expires the cached copy."""

_TINY_COOLDOWN = timedelta(milliseconds=1)
"""An unknown-key-ID cooldown short enough that a brief sleep ends it."""


def _numeric_date(offset: timedelta) -> int:
    """Return a JWT numeric date ``offset`` away from now.

    Skew tests set one time-based claim at a time, rather than shifting the
    whole token with ``issued_at``, so each test pins the claim it is about
    and leaves the others plainly valid.
    """
    return int((datetime.now(tz=UTC) + offset).timestamp())


def _make_verifier(
    http_client: AsyncClient, **overrides: Any
) -> GitHubOidcVerifier:
    """Build a verifier bound to the test HTTP client.

    Defaults are the production ones apart from the audience, which has no
    production default; a test overrides only the knob it is about.
    """
    kwargs: dict[str, Any] = {
        "http_client": http_client,
        "audience": TEST_AUDIENCE,
        "logger": structlog.get_logger("ook"),
    }
    kwargs.update(overrides)
    return GitHubOidcVerifier(**kwargs)


@pytest.mark.asyncio
async def test_valid_token_yields_provenance_claims(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A token signed by a published key verifies and yields its claims."""
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    token = key.mint(
        repository="lsst-sqre/documenteer",
        run_id="42",
        workflow_ref="lsst-sqre/documenteer/.github/workflows/ci.yaml@main",
    )

    claims = await _make_verifier(http_client).verify(token)

    assert claims.repository == "lsst-sqre/documenteer"
    assert claims.run_id == "42"
    assert claims.workflow_ref == (
        "lsst-sqre/documenteer/.github/workflows/ci.yaml@main"
    )


@pytest.mark.asyncio
async def test_any_repository_is_accepted(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A valid token from an unrelated repository verifies.

    OIDC here is provenance attestation, not authorization: there is no
    repository allowlist, so the claim is recorded rather than judged.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    token = key.mint(repository="some-org/some-repo")

    claims = await _make_verifier(http_client).verify(token)

    assert claims.repository == "some-org/some-repo"


@pytest.mark.asyncio
async def test_bad_signature_is_rejected(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A token signed by a different key than the one it names is rejected."""
    published = GitHubOidcSigningKey()
    impostor = GitHubOidcSigningKey(published.kid, key_name="impostor")
    JwksMock(respx_mock, [published])

    with pytest.raises(InvalidOidcTokenError):
        await _make_verifier(http_client).verify(impostor.mint())


@pytest.mark.asyncio
async def test_wrong_audience_is_rejected(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A token minted for another audience is rejected."""
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    token = key.mint(audience="https://other.example.com/ook")

    with pytest.raises(InvalidOidcTokenError):
        await _make_verifier(http_client).verify(token)


@pytest.mark.asyncio
async def test_wrong_issuer_is_rejected(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A token from an issuer other than GitHub Actions is rejected."""
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    token = key.mint(issuer="https://token.actions.example.com")

    with pytest.raises(InvalidOidcTokenError):
        await _make_verifier(http_client).verify(token)


@pytest.mark.asyncio
async def test_expired_token_is_rejected(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A token whose expiry has passed is rejected."""
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    token = key.mint(lifetime=timedelta(minutes=-5))

    with pytest.raises(InvalidOidcTokenError):
        await _make_verifier(http_client).verify(token)


@pytest.mark.asyncio
async def test_future_iat_within_the_leeway_is_accepted(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A token issued a few seconds ahead of Ook's clock still verifies.

    GitHub stamps ``iat`` from its own clock and PyJWT rejects a
    future-dated one outright, so without a leeway a clock lag of a single
    second would surface in a client's CI as an unexplainable contribution
    rejection.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    token = key.mint(
        repository="org/skewed-clock",
        extra_claims={"iat": _numeric_date(timedelta(seconds=5))},
    )

    claims = await _make_verifier(http_client).verify(token)

    assert claims.repository == "org/skewed-clock"


@pytest.mark.asyncio
async def test_future_nbf_within_the_leeway_is_accepted(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """The same skew tolerance covers a not-before claim.

    ``nbf`` is checked in the same shape as ``iat`` and against the same
    lagging clock, so tolerating skew on one and not the other would leave
    the failure mode in place.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    token = key.mint(
        repository="org/skewed-clock",
        extra_claims={"nbf": _numeric_date(timedelta(seconds=5))},
    )

    claims = await _make_verifier(http_client).verify(token)

    assert claims.repository == "org/skewed-clock"


@pytest.mark.asyncio
async def test_future_iat_beyond_the_leeway_is_rejected(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """Skew tolerance widens the validity window; it does not remove it.

    A token issued minutes into the future is not a clock that drifted, so
    it stays rejected.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    token = key.mint(extra_claims={"iat": _numeric_date(timedelta(minutes=5))})

    with pytest.raises(InvalidOidcTokenError):
        await _make_verifier(http_client).verify(token)


@pytest.mark.asyncio
async def test_expiry_beyond_the_leeway_is_rejected(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """The skew tolerance on the expiry side is bounded to seconds.

    PyJWT applies one leeway to every time-based claim, so tolerating a
    future ``iat`` necessarily tolerates a just-past ``exp`` too. This
    pins how far that reaches: a token a minute past expiry is already
    outside it, which is what keeps the accepted cost the tenth of a
    token's lifetime the service documents rather than an open-ended one.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    token = key.mint(lifetime=timedelta(seconds=-60))

    with pytest.raises(InvalidOidcTokenError):
        await _make_verifier(http_client).verify(token)


@pytest.mark.asyncio
async def test_zero_leeway_rejects_a_skewed_token(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """The tolerated skew comes from the constructor, not a constant.

    Turning it off restores PyJWT's own behavior, which is what shows the
    accepted tokens above are accepted because of the leeway.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    token = key.mint(extra_claims={"iat": _numeric_date(timedelta(seconds=5))})
    verifier = _make_verifier(http_client, token_leeway=timedelta(0))

    with pytest.raises(InvalidOidcTokenError):
        await verifier.verify(token)


@pytest.mark.asyncio
async def test_missing_provenance_claim_is_rejected(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A token carrying no ``repository`` claim is rejected."""
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    token = key.mint(drop_claims=["repository"])

    with pytest.raises(InvalidOidcTokenError):
        await _make_verifier(http_client).verify(token)


@pytest.mark.asyncio
async def test_non_string_provenance_claim_is_rejected(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A provenance claim that is present but not a string is rejected.

    Presence is all the JWT library checks, so the type check is the
    service's own and needs its own test.
    """
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    token = key.mint(run_id=1234567890)

    with pytest.raises(InvalidOidcTokenError):
        await _make_verifier(http_client).verify(token)


@pytest.mark.asyncio
async def test_malformed_token_is_rejected(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A string that is not a JWT at all is rejected without a JWKS fetch."""
    jwks = JwksMock(respx_mock, [GitHubOidcSigningKey()])

    with pytest.raises(InvalidOidcTokenError):
        await _make_verifier(http_client).verify("not-a-jwt")

    assert jwks.call_count == 0


@pytest.mark.asyncio
async def test_token_without_key_id_is_rejected(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A token whose header names no key is rejected."""
    key = GitHubOidcSigningKey()
    JwksMock(respx_mock, [key])
    token = key.mint(kid="")

    with pytest.raises(InvalidOidcTokenError):
        await _make_verifier(http_client).verify(token)


@pytest.mark.asyncio
async def test_unknown_key_id_refetches_once_then_fails(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """An unknown key ID costs exactly one refetch before the rejection."""
    key = GitHubOidcSigningKey()
    jwks = JwksMock(respx_mock, [key])
    token = key.mint(kid="a-key-github-never-published")

    with pytest.raises(InvalidOidcTokenError):
        await _make_verifier(http_client).verify(token)

    assert jwks.call_count == 2


@pytest.mark.asyncio
async def test_unknown_key_ids_share_one_refetch_per_cooldown(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """Serial unknown key IDs cost one refetch, not one refetch each.

    Without the cooldown a stream of tokens naming keys GitHub never
    published would turn into a stream of requests to GitHub, one per
    token, which is exactly the amplification the refresh path must not
    hand to a caller.
    """
    key = GitHubOidcSigningKey()
    jwks = JwksMock(respx_mock, [key])
    verifier = _make_verifier(http_client)

    for _ in range(2):
        with pytest.raises(InvalidOidcTokenError):
            await verifier.verify(key.mint(kid="a-key-github-never-published"))

    # The cold fetch plus the window's one refetch, and nothing more.
    assert jwks.call_count == 2


@pytest.mark.asyncio
async def test_rotated_key_is_picked_up_by_the_refetch(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A key published since the last fetch verifies on the refetch.

    This is the point of the unknown-kid refresh path: GitHub rotating its
    signing keys mid-TTL must not reject good tokens until the TTL lapses.
    """
    old_key = GitHubOidcSigningKey("github-oidc-key-1")
    new_key = GitHubOidcSigningKey("github-oidc-key-2")
    jwks = JwksMock(respx_mock, [old_key])
    verifier = _make_verifier(http_client)

    # Warm the cache with a document that does not yet publish the new key.
    await verifier.verify(old_key.mint())
    assert jwks.call_count == 1

    jwks.keys.append(new_key)
    claims = await verifier.verify(new_key.mint(repository="org/rotated"))

    assert claims.repository == "org/rotated"
    assert jwks.call_count == 2


@pytest.mark.asyncio
async def test_rotated_key_is_picked_up_after_the_cooldown(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """The cooldown delays the rotation refetch rather than cancelling it.

    A caller presenting invented key IDs can spend the window's one
    refetch, but only that window's: the next miss after it ends refetches
    again, so a key GitHub rotated in still starts verifying without
    waiting out the whole JWKS TTL.
    """
    old_key = GitHubOidcSigningKey("github-oidc-key-1")
    new_key = GitHubOidcSigningKey("github-oidc-key-2")
    jwks = JwksMock(respx_mock, [old_key])
    verifier = _make_verifier(
        http_client, jwks_kid_miss_cooldown=_TINY_COOLDOWN
    )

    # Spend the window on a token naming a key that will never exist.
    with pytest.raises(InvalidOidcTokenError):
        await verifier.verify(old_key.mint(kid="a-key-github-never-published"))
    assert jwks.call_count == 2

    jwks.keys.append(new_key)
    await asyncio.sleep(0.01)
    claims = await verifier.verify(new_key.mint(repository="org/rotated"))

    assert claims.repository == "org/rotated"
    assert jwks.call_count == 3


@pytest.mark.asyncio
async def test_jwks_is_cached_within_the_ttl(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """Repeated verifications inside the TTL make one upstream request."""
    key = GitHubOidcSigningKey()
    jwks = JwksMock(respx_mock, [key])
    verifier = _make_verifier(http_client)

    await verifier.verify(key.mint())
    await verifier.verify(key.mint())

    assert jwks.call_count == 1


@pytest.mark.asyncio
async def test_jwks_is_refetched_after_the_ttl(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A verification after the TTL lapses refetches the JWKS."""
    key = GitHubOidcSigningKey()
    jwks = JwksMock(respx_mock, [key])
    verifier = _make_verifier(http_client, jwks_ttl=_TINY_TTL)

    await verifier.verify(key.mint())
    await asyncio.sleep(0.01)
    await verifier.verify(key.mint())

    assert jwks.call_count == 2


@pytest.mark.asyncio
async def test_concurrent_cold_verifications_fetch_once(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """Simultaneous first verifications share a single JWKS fetch."""
    key = GitHubOidcSigningKey()
    jwks = JwksMock(respx_mock, [key])
    verifier = _make_verifier(http_client)

    await asyncio.gather(*(verifier.verify(key.mint()) for _ in range(4)))

    assert jwks.call_count == 1


@pytest.mark.asyncio
async def test_unfetchable_jwks_reports_unavailable(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """With no cached keys and a failing fetch, no verdict is reached."""
    key = GitHubOidcSigningKey()
    jwks = JwksMock(respx_mock, [key])
    jwks.status_code = 503

    with pytest.raises(GitHubOidcUnavailableError):
        await _make_verifier(http_client).verify(key.mint())


@pytest.mark.asyncio
async def test_unparsable_jwks_reports_unavailable(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A JWKS document that is not a key set is treated as a failed fetch."""
    key = GitHubOidcSigningKey()
    jwks = JwksMock(respx_mock, [key])
    jwks.body = ["not", "a", "key", "set"]

    with pytest.raises(GitHubOidcUnavailableError):
        await _make_verifier(http_client).verify(key.mint())


@pytest.mark.asyncio
async def test_cached_keys_survive_a_failed_refetch(
    http_client: AsyncClient,
    respx_mock: respx.Router,
) -> None:
    """A failed refetch keeps verifying tokens against the cached keys.

    Signing keys rotate slowly, so a cached document is the right answer
    while GitHub's JWKS endpoint is unreachable.
    """
    key = GitHubOidcSigningKey()
    jwks = JwksMock(respx_mock, [key])
    verifier = _make_verifier(http_client, jwks_ttl=_TINY_TTL)

    await verifier.verify(key.mint())
    jwks.status_code = 503
    await asyncio.sleep(0.01)
    claims = await verifier.verify(key.mint(repository="org/still-verified"))

    assert claims.repository == "org/still-verified"
    assert jwks.call_count == 2


@pytest.mark.asyncio
async def test_factory_provides_github_oidc_verifier(
    factory: Factory,
) -> None:
    """The factory exposes a process-wide verifier on the shared client.

    A singleton because the JWKS cache lives in memory: a per-request
    verifier would refetch GitHub's keys on every request.
    """
    verifier = factory.github_oidc_verifier

    assert isinstance(verifier, GitHubOidcVerifier)
    assert factory.github_oidc_verifier is verifier
    assert verifier._http_client is factory.http_client
    assert verifier._audience == config.oidc_audience
