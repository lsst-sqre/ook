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
    from ook.domain.authors import Author

__all__ = [
    "DocumentParsingError",
    "DuplicateOrcidError",
    "LtdSlugClassificationError",
    "NotFoundError",
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


class NotFoundError(ClientRequestError):
    """Raised when a resource is not found."""

    error = "not_found"
    status_code = status.HTTP_404_NOT_FOUND


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
