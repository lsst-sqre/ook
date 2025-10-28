"""Slack notification service."""

from __future__ import annotations

from typing import TYPE_CHECKING

from safir.slack.webhook import (  # type: ignore[attr-defined]
    SlackMessage,
    SlackWebhookClient,
)
from structlog import get_logger

if TYPE_CHECKING:
    from safir.slack.webhook import (  # type: ignore[attr-defined]
        SlackException,
    )

    from ook.config import Configuration
    from ook.domain.authors import Author

__all__ = ["SlackNotificationService"]


class SlackNotificationService:
    """Service for sending Slack notifications about Ook operations.

    Parameters
    ----------
    config
        The application configuration.
    """

    def __init__(self, config: Configuration) -> None:
        self.config = config
        self.client: SlackWebhookClient | None = None

        if config.slack_webhook:
            logger = get_logger(__name__)
            self.client = SlackWebhookClient(
                str(config.slack_webhook.get_secret_value()),
                "Ook",
                logger,
            )

    async def notify_stale_authors(
        self,
        stale_authors: list[Author],
        git_ref: str,
    ) -> None:
        """Send notification about stale author entries.

        Parameters
        ----------
        stale_authors
            List of stale author entries that no longer exist in the
            upstream authordb.yaml but still exist in Ook's database.
        git_ref
            Git reference being ingested (e.g., commit SHA or branch name).
        """
        if not self.client:
            return

        if not stale_authors:
            return

        # Build message with list of stale authors
        author_list = "\n".join(
            f"• `{a.internal_id}` ("
            f"{a.given_name + ' ' if a.given_name else ''}{a.surname})"
            + (f" - ORCID: {a.orcid}" if a.orcid else "")
            for a in stale_authors[:10]  # Limit to first 10
        )

        if len(stale_authors) > 10:
            author_list += f"\n... and {len(stale_authors) - 10} more"

        message_text = (
            f"⚠️ *Stale Author Entries Detected*\n\n"
            f"Found {len(stale_authors)} author(s) in Ook database "
            f"that no longer exist in lsst-texmf authordb.yaml "
            f"(ref: `{git_ref}`):\n\n"
            f"{author_list}\n\n"
            f"These entries may need manual review. They could represent:\n"
            f"• Author ID changes (e.g., ID renamed)\n"
            f"• Removed authors\n"
            f"• Temporary upstream errors\n\n"
            f"*Action needed:* Review these entries and delete if appropriate."
        )

        message = SlackMessage(message=message_text)
        await self.client.post(message)

    async def post_exception(self, exception: SlackException) -> None:
        """Post an exception to Slack using Safir's exception handling.

        This method is used to report SlackException instances (like
        DuplicateOrcidError) which format themselves via to_slack().

        Parameters
        ----------
        exception
            The exception to post (must be a SlackException subclass).
        """
        if not self.client:
            return

        await self.client.post_exception(exception)
