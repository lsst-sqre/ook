"""Domain models for GitHub Actions OIDC provenance."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["GitHubOidcClaims"]


@dataclass(frozen=True, slots=True)
class GitHubOidcClaims:
    """The provenance a verified GitHub Actions OIDC id-token attests to.

    Only the claims Ook records as provenance are modelled. The token's own
    machinery — issuer, audience, expiry, signature — is consumed by
    `~ook.services.githuboidc.GitHubOidcVerifier` and never reaches here, so
    an instance of this class exists only for a token that has already
    passed every check: constructing one is the statement that GitHub
    vouched for these three facts.

    They are attestation, not authorization. Ook accepts a valid token from
    any repository, because what the token establishes is *where a result
    was observed from*, not whether the caller may submit one — that is the
    Gafaelfawr scope at the ingress.
    """

    repository: str
    """The ``owner/name`` of the repository whose workflow minted the token.

    The provenance a contributed result is attributed to, and what a check
    report renders as "externally verified by <repository> CI".
    """

    run_id: str
    """The identifier of the workflow run that minted the token.

    A string rather than an integer because GitHub mints it as one, and
    because it is only ever stored, logged, and pasted back into a run URL.
    """

    workflow_ref: str
    """The fully-qualified reference of the workflow that minted the token.

    Shaped ``owner/name/.github/workflows/file.yaml@ref``, so it names both
    the workflow definition and the git ref it ran from.
    """
