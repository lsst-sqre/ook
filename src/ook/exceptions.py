"""Ook's exceptions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import status
from safir.fastapi import ClientRequestError
from safir.slack.webhook import (  # type: ignore[attr-defined]
    SlackException,
    SlackMessage,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ook.domain.authors import Author

__all__ = [
    "ConflictError",
    "ConflictingQueryParametersError",
    "DocumentParsingError",
    "DuplicateOrcidError",
    "GitHubOidcUnavailableError",
    "InvalidInventoryUrlError",
    "InvalidOidcTokenError",
    "InvalidOrcidError",
    "InventoryParseError",
    "LinkCheckTooManyUrlsError",
    "LtdSlugClassificationError",
    "NotFoundError",
    "UpstreamInventoryError",
]


class LtdSlugClassificationError(Exception):
    """An error occurred during classification and ingest queueing for an
    LTD document.
    """

    def __init__(
        self,
        message: str,
        *,
        product_slug: str,
        edition_slug: str,
        error: Exception | None = None,
    ) -> None:
        """Initialize the exception.

        Parameters
        ----------
        message
            A message describing the error.
        """
        self.product_slug = product_slug
        self.edition_slug = edition_slug
        self.error = error
        super().__init__(message)

    def __str__(self) -> str:
        message = (
            f"Unable to queue ingest for LTD slug: {self.product_slug} "
            f"({self.edition_slug}): {super().__str__()}"
        )
        if self.error is not None:
            message += f"\n\n{self.error}"
        return message


class DocumentParsingError(Exception):
    """Raised when there is a document parsing error."""


class InventoryParseError(Exception):
    """Raised when an intersphinx ``objects.inv`` payload cannot be parsed.

    Every way a payload can fail to parse is this one class, so the ingest
    service's per-source failure policy can record the failure and move on
    to the next source. Nothing about a malformed inventory is the client's
    fault -- the bytes come from an upstream documentation site -- so this
    is a plain exception rather than a `~safir.fastapi.ClientRequestError`.
    """


class NotFoundError(ClientRequestError):
    """Raised when a resource is not found."""

    error = "not_found"
    status_code = status.HTTP_404_NOT_FOUND


class LinkCheckTooManyUrlsError(ClientRequestError):
    """Raised when a link-check submission exceeds the URL cap."""

    error = "too_many_urls"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class InvalidInventoryUrlError(ClientRequestError):
    """Raised when an intersphinx inventory URL fails the SSRF guard.

    A URL is rejected if no parser accepts it, if it does not use ``https``,
    or if its host resolves to a private, link-local, or loopback address.
    Rejected URLs are never fetched from upstream and never stored in the
    cache.

    Each of those is a fact about the URL the client chose, and so
    something the client can fix. A well-formed URL whose host merely will
    not resolve is not: that is reported as an `UpstreamInventoryError`
    instead, matching how the same failure on a redirect hop is classified.
    """

    error = "invalid_inventory_url"
    status_code = status.HTTP_400_BAD_REQUEST


class UpstreamInventoryError(ClientRequestError):
    """Raised when an upstream intersphinx inventory fetch fails on a cold
    miss.

    An upstream 4xx/5xx response, timeout, or connection error on a cold
    miss (when no cached content exists to serve) maps to a 502 with a
    detail message the client can log. The failure is negatively cached for
    ``OOK_INTERSPHINX_NEGATIVE_TTL`` so a repeat request inside the window
    raises this again without re-contacting upstream.
    """

    error = "upstream_inventory_error"
    status_code = status.HTTP_502_BAD_GATEWAY


class InvalidOidcTokenError(ClientRequestError):
    """Raised when a GitHub Actions OIDC id-token fails provenance
    verification.

    Every way a presented token can fail is this one class: a token that is
    not a JWT at all, one carrying no key ID or one no published GitHub key
    matches, a bad signature, the wrong audience or issuer, an expired or
    not-yet-valid token, and one missing a provenance claim. They are one
    class because the caller can do exactly one thing about any of them —
    mint a fresh token from a GitHub Actions workflow with the right
    audience — and because distinguishing them in the response would tell an
    attacker probing with forged tokens which part of the forgery to fix.
    The specific reason travels in the message for the client's own logs and
    in Ook's structured logs for operators.

    A 422 rather than a 401: the ingress already authenticated the request
    against Gafaelfawr's link-check submission scope, and this token is a
    field of the request body attesting to *where* the contributed results
    were observed. The failure is therefore a fact about the payload, and
    raising it with the ``location`` and ``field_path`` of that field lands
    the error in the same shape FastAPI produces for a body field failing
    its own validation.
    """

    error = "invalid_oidc_token"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class GitHubOidcUnavailableError(ClientRequestError):
    """Raised when GitHub's OIDC signing keys cannot be obtained at all.

    Verification needs GitHub's published JWKS, and a fetch of it can fail
    (a 5xx, a timeout, a connection error, an unparsable document). When a
    previously-fetched key set is cached the failure is invisible: the
    stale copy keeps serving, because JWKS rotation is slow and an old copy
    verifies today's tokens. This is raised only for the cold case, where
    there is no cached copy to fall back on and so no way to tell a good
    token from a forged one.

    Deliberately not `InvalidOidcTokenError`: the client's token may be
    perfectly good, and telling them it was rejected would send them
    re-minting a token that was never the problem. A 502, matching
    `UpstreamInventoryError`, since both report that an upstream Ook depends
    on failed to answer. The condition is transient, so the request is worth
    retrying.
    """

    error = "github_oidc_unavailable"
    status_code = status.HTTP_502_BAD_GATEWAY


class ConflictError(ClientRequestError):
    """Raised when a request conflicts with the current state of a
    resource.
    """

    error = "conflict"
    status_code = status.HTTP_409_CONFLICT


class ConflictingQueryParametersError(ClientRequestError):
    """Raised when a request combines query parameters that cannot both
    apply.

    Two parameters conflict when there is no single response that honours
    both — they select different models, different match semantics, or one
    describes machinery the other does not have. Answering such a request by
    letting one parameter win would hide the client's bug behind a plausible
    body, so the request is refused instead.

    Raise this with the ``location`` and ``field_path`` of the parameter the
    client should drop, so the error lands in the same shape FastAPI produces
    for a parameter that fails its own validation.
    """

    error = "conflicting_query_parameters"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class DuplicateOrcidError(SlackException):
    """Raised when attempting to create an author with a duplicate ORCID.

    This exception automatically formats and sends a Slack notification
    when reported via SlackWebhookClient.post_exception().

    Parameters
    ----------
    orcid
        The ORCID identifier that is duplicated.
    new_authors
        List of new author(s) attempting to use the same ORCID.
    existing_author
        The existing author in the database with this ORCID.
    git_ref
        Git reference being ingested (for error reporting context).
    """

    def __init__(
        self,
        orcid: str,
        new_authors: list[Author],
        existing_author: Author,
        git_ref: str,
    ) -> None:
        self.orcid = orcid
        self.new_authors = new_authors
        self.existing_author = existing_author
        self.git_ref = git_ref

        # Note: message should not contain sensitive information
        existing_name = (
            f"{existing_author.given_name} {existing_author.surname}"
            if existing_author.given_name
            else existing_author.surname
        )
        super().__init__(
            f"ORCID {orcid} already exists for author "
            f"{existing_author.internal_id} ({existing_name}). "
            f"Cannot create new author(s): "
            + ", ".join(
                f"{a.internal_id} ("
                f"{a.given_name + ' ' if a.given_name else ''}{a.surname})"
                for a in new_authors
            )
        )

    def to_slack(self) -> SlackMessage:
        """Format exception as a Slack message with detailed context.

        Returns
        -------
        SlackMessage
            Formatted Slack message with duplicate ORCID details.
        """
        # Build list of new authors attempting to use the ORCID
        new_authors_list = "\n".join(
            f"• `{a.internal_id}` ("
            f"{a.given_name + ' ' if a.given_name else ''}{a.surname})"
            for a in self.new_authors
        )

        existing_name = (
            f"{self.existing_author.given_name} {self.existing_author.surname}"
            if self.existing_author.given_name
            else self.existing_author.surname
        )

        message_text = (
            f"🚨 *Author Ingest Failed: Duplicate ORCID*\n\n"
            f"ORCID `{self.orcid}` already exists in database.\n\n"
            f"*Existing entry:*\n"
            f"• ID: `{self.existing_author.internal_id}`\n"
            f"• Name: {existing_name}\n\n"
            f"*New entry attempting to use same ORCID:*\n"
            f"{new_authors_list}\n\n"
            f"*Git ref:* `{self.git_ref}`\n\n"
            f"*Likely cause:* Author ID was renamed in lsst-texmf but old "
            f"entry still exists in Ook.\n\n"
            f"*Resolution:* Review and delete the stale entry if appropriate, "
            f"then re-run ingest."
        )

        return SlackMessage(message=message_text)


class InvalidOrcidError(SlackException):
    """Raised when an ingested author's ORCID cannot be normalized.

    The ORCID lookup on ``GET /ook/authors`` matches with a plain equality
    so it can ride the ``uq_author_orcid`` index, which is only correct
    while every stored ORCID is the bare, uppercase identifier. The ingest
    is the sole write path for ``author.orcid``, so it is where that
    invariant is established: an incoming value that
    `~ook.domain.authors.normalize_orcid` refuses aborts the run before
    anything is written.

    Every offender in the run is reported at once rather than one per
    attempt, so a single upstream fix round clears the ingest.

    Parameters
    ----------
    authors
        The authors whose ORCIDs were refused, each still carrying the
        rejected value in its ``orcid`` attribute.
    git_ref
        Git reference being ingested (for error reporting context).
    """

    def __init__(self, authors: Sequence[Author], git_ref: str) -> None:
        self.authors = list(authors)
        self.git_ref = git_ref

        # Note: message should not contain sensitive information
        super().__init__(
            "Cannot ingest author(s) with an unparsable ORCID: "
            + ", ".join(f"{a.internal_id} ({a.orcid!r})" for a in self.authors)
        )

    def to_slack(self) -> SlackMessage:
        """Format exception as a Slack message with detailed context.

        Returns
        -------
        SlackMessage
            Formatted Slack message naming every rejected ORCID.
        """
        authors_list = "\n".join(
            f"• `{a.internal_id}` ("
            f"{a.given_name + ' ' if a.given_name else ''}{a.surname}): "
            f"`{a.orcid}`"
            for a in self.authors
        )

        message_text = (
            f"🚨 *Author Ingest Failed: Invalid ORCID*\n\n"
            f"{len(self.authors)} author(s) carry an ORCID that is not a "
            f"valid identifier, so no authors were ingested.\n\n"
            f"*Rejected entries:*\n"
            f"{authors_list}\n\n"
            f"*Git ref:* `{self.git_ref}`\n\n"
            f"*Likely cause:* An ORCID in authordb.yaml is misspelled, "
            f"truncated, or fails its ISO 7064 check digit.\n\n"
            f"*Resolution:* Correct the ORCID(s) in lsst-texmf, then re-run "
            f"ingest."
        )

        return SlackMessage(message=message_text)
