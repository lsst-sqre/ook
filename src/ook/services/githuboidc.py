"""Service that verifies GitHub Actions OIDC id-tokens."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

import httpx
import jwt
from httpx import AsyncClient
from jwt import PyJWK, PyJWKSet
from structlog.stdlib import BoundLogger

from ook.domain.githuboidc import GitHubOidcClaims
from ook.exceptions import GitHubOidcUnavailableError, InvalidOidcTokenError

__all__ = [
    "GITHUB_OIDC_ISSUER",
    "GITHUB_OIDC_JWKS_URL",
    "GitHubOidcVerifier",
]


GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
"""Issuer of every GitHub Actions OIDC id-token."""

GITHUB_OIDC_JWKS_URL = f"{GITHUB_OIDC_ISSUER}/.well-known/jwks"
"""Where GitHub publishes the public keys those tokens are signed with.

The ``jwks_uri`` of the issuer's OpenID configuration, spelled out rather
than discovered: it is a stable, documented URL, and discovering it would
cost a second upstream round trip on every cold start to learn a constant.
"""

_ALGORITHMS = ["RS256"]
"""Signature algorithms accepted for an id-token.

An allowlist of exactly what GitHub signs with, which is what stops a token
presenting ``alg: none`` or an HMAC algorithm from being verified against a
public key that an attacker also holds.
"""

_REQUIRED_CLAIMS = [
    "aud",
    "exp",
    "iat",
    "iss",
    "repository",
    "run_id",
    "workflow_ref",
]
"""Claims a token must carry to be verifiable at all.

The four registered claims make the token's own validation meaningful — a
token with no ``exp`` never expires, one with no ``aud`` was minted for
nobody — and the three GitHub claims are the provenance the contributions
endpoint exists to record. A token missing any of them is rejected before
its payload is read, so no caller downstream has to handle a claim that
turned out not to be there.
"""


class GitHubOidcVerifier:
    """Verifies GitHub Actions OIDC id-tokens and returns their provenance.

    A workflow running in GitHub Actions can ask GitHub for a short-lived
    id-token naming the repository, run, and workflow that requested it.
    GitHub signs it with a key published in a JWKS document, so anyone
    holding that document can confirm the token is genuine without talking
    to GitHub per request. That is what this service does: it fetches and
    caches the JWKS, then checks each presented token's signature, issuer,
    audience, and expiry before handing back the three provenance claims.

    What it establishes is *provenance*, not authorization. A valid token
    from any repository is accepted, because the question it answers is
    "where was this result observed from", and the question of whether the
    caller may submit results at all is already answered by the Gafaelfawr
    scope at the ingress. There is deliberately no repository allowlist.

    This must be a process-wide singleton: the JWKS cache lives in memory,
    so a per-request instance would refetch GitHub's keys on every request
    and the TTL would never do anything.

    Parameters
    ----------
    http_client
        Shared HTTP client, used to fetch the JWKS document.
    audience
        The audience a token must be minted for — this deployment's public
        base URL. Checking it is what stops a token minted for one Ook
        environment (or for an unrelated service that also accepts GitHub
        OIDC) being replayed against this one.
    logger
        The logger.
    jwks_ttl
        How long a fetched JWKS document is served before it is refetched.
        Rotation is also handled off this schedule — see `verify` — so this
        governs how quickly a *withdrawn* key stops verifying tokens, which
        needs no urgency.
    jwks_kid_miss_cooldown
        Minimum interval between the off-schedule refetches that a token
        naming an unknown key ID triggers. It is what stops that path
        amplifying a stream of tokens carrying invented key IDs into a
        stream of requests to GitHub; it costs a genuinely rotated-in key
        at most this long to start verifying.
    token_leeway
        Clock skew tolerated when checking a token's time-based claims —
        see `_decode`, which explains why it applies to expiry as well as
        to the not-yet-valid checks. Thirty seconds rather than a minute
        because the nodes involved are NTP-synced and realistic skew is
        sub-second: this covers it with a wide margin, and skew beyond it
        is a monitoring problem that should be visible rather than
        silently absorbed.
    issuer
        The issuer a token must name. Injectable for tests; there is one
        real value.
    jwks_url
        Where the signing keys are published. Injectable for tests; there
        is one real value.
    request_timeout
        Time budget for one JWKS fetch. Short, because a client is waiting
        on it and a stale cached copy (when there is one) is the better
        answer than a long stall.
    """

    def __init__(
        self,
        *,
        http_client: AsyncClient,
        audience: str,
        logger: BoundLogger,
        jwks_ttl: timedelta = timedelta(hours=1),
        jwks_kid_miss_cooldown: timedelta = timedelta(seconds=60),
        token_leeway: timedelta = timedelta(seconds=30),
        issuer: str = GITHUB_OIDC_ISSUER,
        jwks_url: str = GITHUB_OIDC_JWKS_URL,
        request_timeout: timedelta = timedelta(seconds=10),
    ) -> None:
        self._http_client = http_client
        self._audience = audience
        self._logger = logger
        self._jwks_ttl = jwks_ttl.total_seconds()
        self._kid_miss_cooldown = jwks_kid_miss_cooldown.total_seconds()
        self._token_leeway = token_leeway.total_seconds()
        self._issuer = issuer
        self._jwks_url = jwks_url
        self._request_timeout = request_timeout.total_seconds()
        self._jwks: PyJWKSet | None = None
        self._jwks_fetched_at = 0.0
        self._kid_miss_refreshed_at: float | None = None
        self._lock = asyncio.Lock()

    async def verify(self, token: str) -> GitHubOidcClaims:
        """Verify an id-token and return the provenance it attests to.

        A token whose key ID is not in the cached JWKS triggers a refetch
        before the token is rejected. That is what makes a GitHub key
        rotation invisible to clients: a token signed by a key minted since
        the last fetch verifies on the retry rather than failing for the
        rest of the TTL.

        That refetch fires at most once per ``jwks_kid_miss_cooldown``, and
        a token arriving inside the window is judged against the keys
        already in hand. A stream of forged tokens carrying invented key
        IDs therefore collapses to one request to GitHub per window instead
        of one per token, while a genuinely rotated-in key is still picked
        up by the first miss after the window ends.

        Parameters
        ----------
        token
            The encoded id-token, as presented by the client.

        Returns
        -------
        GitHubOidcClaims
            The repository, run, and workflow the token attests to.

        Raises
        ------
        InvalidOidcTokenError
            Raised if the token is malformed, signed by an unpublished key,
            signed badly, minted for another audience or by another issuer,
            expired or not yet valid, or missing a required claim.
        GitHubOidcUnavailableError
            Raised if GitHub's signing keys could not be fetched and none
            were cached, so no token can be judged either way.
        """
        kid = self._unverified_kid(token)
        jwks = await self._get_jwks()
        key = _find_key(jwks, kid)
        if key is None:
            jwks = await self._refresh_jwks(stale=jwks)
            key = _find_key(jwks, kid)
        if key is None:
            self._logger.info(
                "Rejected an OIDC token signed by an unpublished key",
                kid=kid,
                jwks_url=self._jwks_url,
            )
            raise InvalidOidcTokenError(
                "No GitHub OIDC signing key matches the token's key ID"
            )
        return _to_claims(self._decode(token, key))

    def _unverified_kid(self, token: str) -> str:
        """Read the key ID from the token's unverified header.

        Reading the header before any signature is checked is unavoidable —
        the header is what names the key to check the signature *with* — so
        nothing but the key ID is taken from it, and the key ID is only
        ever used to look up a key GitHub published. A forged header can
        therefore select a real key or no key, and never introduce one.
        """
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as e:
            raise InvalidOidcTokenError(
                f"The OIDC token is not a well-formed JWT: {e!s}"
            ) from e
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise InvalidOidcTokenError(
                "The OIDC token's header carries no key ID"
            )
        return kid

    def _decode(self, token: str, key: PyJWK) -> dict[str, Any]:
        """Check the token against ``key`` and return its payload.

        Signature, algorithm, audience, issuer, expiry, and claim presence
        are all checked here, in one call, so none of them can be
        accidentally skipped by a later edit that reorders the checks.

        The time-based claims are checked with ``token_leeway`` of slack,
        because GitHub stamps ``iat`` and ``nbf`` from *its* clock and this
        process judges them by its own: without the leeway, Ook's clock
        lagging GitHub's by a second is enough to reject a genuine token as
        not yet valid, which reaches a client as a contribution rejection
        it cannot explain or retry its way out of.

        That slack is a single value covering ``iat``, ``nbf``, *and*
        ``exp`` alike — PyJWT has no way to scope it to the not-yet-valid
        checks — so a token also stays acceptable for ``token_leeway``
        past its expiry. That is the accepted cost here: against the ~5
        minute lifetime of a GitHub Actions id-token it extends by about a
        tenth a window an attacker holding a captured token could already
        use in full, and the token attests *provenance*, not
        authorization — what permits the write at all is the Gafaelfawr
        scope at the ingress. RFC 7519 §4.1.4 allows exactly this: "a
        small leeway, usually no more than a few minutes, to account for
        clock skew".
        """
        try:
            return jwt.decode(
                token,
                key=key,
                algorithms=_ALGORITHMS,
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._token_leeway,
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.InvalidTokenError as e:
            self._logger.info(
                "Rejected an invalid OIDC token", error=str(e), kid=key.key_id
            )
            raise InvalidOidcTokenError(
                f"The OIDC token failed verification: {e!s}"
            ) from e

    async def _get_jwks(self) -> PyJWKSet:
        """Return the cached key set, fetching it if absent or expired."""
        async with self._lock:
            if self._jwks is not None and not self._is_expired():
                return self._jwks
            return await self._fetch_jwks()

    async def _refresh_jwks(self, *, stale: PyJWKSet) -> PyJWKSet:
        """Refetch the key set after ``stale`` failed to supply a key ID.

        Skips the fetch when another request already replaced ``stale``
        while this one waited for the lock: that request refetched for the
        same reason, so its result is the fresh copy this one wanted, and
        fetching again would let concurrent unknown-kid tokens multiply
        into concurrent requests to GitHub.

        Skips it too when this path already refetched within the cooldown,
        which is what bounds the *serial* case the lock cannot see: without
        it, tokens naming invented key IDs arriving one after another each
        buy their own request to GitHub.

        The window is marked spent before the fetch rather than after, so a
        fetch that fails still costs the caller the window. The point is to
        bound requests to GitHub, and a failing endpoint is precisely when
        retrying per token is worst.
        """
        async with self._lock:
            if self._jwks is not None and self._jwks is not stale:
                return self._jwks
            if self._is_in_kid_miss_cooldown():
                self._logger.debug(
                    "Skipped a JWKS refetch for an unknown key ID",
                    jwks_url=self._jwks_url,
                )
                return stale
            self._kid_miss_refreshed_at = time.monotonic()
            return await self._fetch_jwks()

    async def _fetch_jwks(self) -> PyJWKSet:
        """Fetch and cache GitHub's JWKS document. Call under the lock."""
        try:
            response = await self._http_client.get(
                self._jwks_url, timeout=self._request_timeout
            )
            response.raise_for_status()
            jwks = _parse_jwks(response)
        except (httpx.HTTPError, TypeError, ValueError, jwt.PyJWTError) as e:
            return self._fall_back_to_stale(e)
        self._jwks = jwks
        self._jwks_fetched_at = time.monotonic()
        self._logger.debug(
            "Fetched GitHub's OIDC signing keys",
            jwks_url=self._jwks_url,
            key_count=len(jwks.keys),
        )
        return jwks

    def _fall_back_to_stale(self, error: Exception) -> PyJWKSet:
        """Handle a failed JWKS fetch, serving the cached copy if there is
        one.

        The cached copy is not re-dated, so the next verification retries
        the fetch rather than waiting out a TTL that a failure never earned.
        """
        if self._jwks is not None:
            self._logger.warning(
                "Serving cached GitHub OIDC signing keys after a failed fetch",
                error=str(error),
                jwks_url=self._jwks_url,
            )
            return self._jwks
        self._logger.warning(
            "Could not fetch GitHub's OIDC signing keys",
            error=str(error),
            jwks_url=self._jwks_url,
        )
        raise GitHubOidcUnavailableError(
            "Could not fetch GitHub's OIDC signing keys, so the token's "
            "provenance could not be verified"
        ) from error

    def _is_expired(self) -> bool:
        """Whether the cached key set has outlived its TTL."""
        return time.monotonic() - self._jwks_fetched_at >= self._jwks_ttl

    def _is_in_kid_miss_cooldown(self) -> bool:
        """Whether an unknown key ID refetched too recently to do it again.

        The mark starts unset rather than at zero because `time.monotonic`
        counts from an arbitrary origin: a process that starts less than a
        cooldown after that origin would otherwise begin life with its
        first refetch already suppressed, and a rotation invisible to it.
        """
        if self._kid_miss_refreshed_at is None:
            return False
        elapsed = time.monotonic() - self._kid_miss_refreshed_at
        return elapsed < self._kid_miss_cooldown


def _parse_jwks(response: httpx.Response) -> PyJWKSet:
    """Parse a JWKS response body into a key set.

    Raises
    ------
    TypeError
        Raised if the body is valid JSON but not a JSON object.
    ValueError
        Raised if the body is not valid JSON at all.
    jwt.PyJWTError
        Raised if the object carries no key this library can use.
    """
    document = response.json()
    if not isinstance(document, dict):
        raise TypeError("The JWKS document is not a JSON object")
    return PyJWKSet.from_dict(document)


def _find_key(jwks: PyJWKSet, kid: str) -> PyJWK | None:
    """Return the published key with this key ID, or None if there is none."""
    try:
        return jwks[kid]
    except KeyError:
        return None


def _to_claims(payload: dict[str, Any]) -> GitHubOidcClaims:
    """Build the provenance model from a verified token payload."""
    return GitHubOidcClaims(
        repository=_string_claim(payload, "repository"),
        run_id=_string_claim(payload, "run_id"),
        workflow_ref=_string_claim(payload, "workflow_ref"),
    )


def _string_claim(payload: dict[str, Any], name: str) -> str:
    """Read a claim that must be a non-empty string.

    ``jwt.decode``'s ``require`` option asserts a claim is present, not that
    it is usable: a claim minted as a number, a list, or an empty string
    satisfies it. Checking the type here means the provenance model is only
    ever constructed from values that can actually be stored and rendered.
    """
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise InvalidOidcTokenError(
            f"The OIDC token's {name} claim is not a non-empty string"
        )
    return value
