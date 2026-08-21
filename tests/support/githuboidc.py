"""Support for testing GitHub Actions OIDC token verification.

GitHub is the only party that can mint a real Actions id-token, so the tests
stand in for it: a locally-generated RSA keypair signs tokens with whatever
claims the test needs, and the matching public key is served through respx at
the JWKS URL the verifier fetches. Nothing here knows about the verifier's
internals — a test drives it entirely through the tokens it mints and the
JWKS document it serves, which is the same surface GitHub presents in
production.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any

import jwt
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import Request, Response
from jwt.algorithms import RSAAlgorithm

from ook.services.githuboidc import GITHUB_OIDC_ISSUER, GITHUB_OIDC_JWKS_URL

__all__ = [
    "DEFAULT_KID",
    "TEST_AUDIENCE",
    "GitHubOidcSigningKey",
    "JwksMock",
]

TEST_AUDIENCE = "https://ook.example.com/ook"
"""Stand-in for Ook's public base URL, the audience tokens are minted for."""

DEFAULT_KID = "github-oidc-key-1"
"""Key ID of the signing key the tests use unless they say otherwise."""

_DEFAULT_REPOSITORY = "lsst-sqre/documenteer"
_DEFAULT_RUN_ID = "1234567890"
_DEFAULT_WORKFLOW_REF = (
    "lsst-sqre/documenteer/.github/workflows/linkcheck.yaml@refs/heads/main"
)

_UNSET = object()
"""Sentinel distinguishing "claim not overridden" from "claim set to None".

`GitHubOidcSigningKey.mint` has to be able to mint a token that is *missing*
a claim, which is not the same as one carrying the claim with a null value,
and `None` cannot express both.
"""


@cache
def _rsa_key(name: str) -> rsa.RSAPrivateKey:
    """Return the RSA private key called ``name``, generating it once.

    Generating a 2048-bit key costs about 80 ms, which is the dominant cost
    of these tests if every construction pays it. Keys are pure test
    scaffolding with no per-test state, so one key per name, cached for the
    session, is both faster and no less isolated.
    """
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class GitHubOidcSigningKey:
    """An RSA keypair standing in for one of GitHub's OIDC signing keys.

    Parameters
    ----------
    kid
        The key ID this key advertises in the JWKS document and stamps into
        the header of every token it signs.
    key_name
        Name of the underlying keypair, defaulting to ``kid``. Pass a
        different name to get a *different* keypair advertising the *same*
        key ID — which is how a bad-signature test builds a token that
        selects a real published key and then fails to verify against it.
    """

    def __init__(self, kid: str = DEFAULT_KID, *, key_name: str = "") -> None:
        self.kid = kid
        self._private_key = _rsa_key(key_name or kid)

    @property
    def jwk(self) -> dict[str, Any]:
        """The public half of this key as a JWKS entry.

        Shaped like GitHub's own: the RFC 7517 RSA parameters plus the
        ``kid``, ``alg``, and ``use`` members a verifier selects keys by.
        """
        jwk = RSAAlgorithm.to_jwk(self._private_key.public_key(), as_dict=True)
        return {**jwk, "kid": self.kid, "alg": "RS256", "use": "sig"}

    def mint(
        self,
        *,
        audience: Any = TEST_AUDIENCE,
        issuer: Any = GITHUB_OIDC_ISSUER,
        repository: Any = _DEFAULT_REPOSITORY,
        run_id: Any = _DEFAULT_RUN_ID,
        workflow_ref: Any = _DEFAULT_WORKFLOW_REF,
        issued_at: datetime | None = None,
        lifetime: timedelta = timedelta(minutes=5),
        kid: str | None = None,
        drop_claims: Iterable[str] = (),
        extra_claims: Mapping[str, Any] | None = None,
    ) -> str:
        """Mint a signed id-token carrying the claims a test needs.

        Every claim defaults to a plausible GitHub Actions value, so a test
        names only the one it is about. ``lifetime`` may be negative, which
        is how an expired token is minted; ``drop_claims`` omits claims
        entirely, which is how a token missing a required claim is minted.

        Returns
        -------
        str
            The encoded, signed JWT.
        """
        now = issued_at or datetime.now(tz=UTC)
        claims: dict[str, Any] = {
            "iss": issuer,
            "aud": audience,
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int((now + lifetime).timestamp()),
            "sub": f"repo:{repository}:ref:refs/heads/main",
            "repository": repository,
            "run_id": run_id,
            "workflow_ref": workflow_ref,
        }
        for name in drop_claims:
            claims.pop(name, None)
        if extra_claims:
            claims.update(extra_claims)
        return jwt.encode(
            claims,
            self._private_key,
            algorithm="RS256",
            headers={"kid": kid if kid is not None else self.kid},
        )


class JwksMock:
    """Serve a JWKS document at GitHub's JWKS URL through respx.

    The served keys stay mutable after registration so a test can rotate
    them — appending the key a new token is signed with, or dropping the
    old one — and have the verifier's next fetch see the change. The
    response status is mutable for the same reason: setting it non-200 is
    how an outage at GitHub's JWKS endpoint is simulated.

    Parameters
    ----------
    respx_mock
        The router to register the JWKS route on.
    keys
        The signing keys the document publishes.
    """

    def __init__(
        self,
        respx_mock: respx.Router,
        keys: Sequence[GitHubOidcSigningKey],
    ) -> None:
        self.keys = list(keys)
        self.status_code = 200
        self.body: Any | None = None
        self.route = respx_mock.get(GITHUB_OIDC_JWKS_URL).mock(
            side_effect=self._respond
        )

    @property
    def call_count(self) -> int:
        """The number of JWKS fetches served so far."""
        return self.route.call_count

    def _respond(self, request: Request) -> Response:
        if self.status_code != 200:
            return Response(self.status_code, text="unavailable")
        if self.body is not None:
            return Response(200, json=self.body)
        return Response(200, json={"keys": [key.jwk for key in self.keys]})
