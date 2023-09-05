"""Ook's exceptions."""

from __future__ import annotations

__all__ = ["LtdSlugClassificationError"]


class LtdSlugClassificationError(Exception):
    """An error occurred during classification and ingest queueing for an
    LTD document.
    """

    def __init__(
        self, message: str, *, product_slug: str, edition_slug: str
    ) -> None:
        """Initialize the exception.

        Parameters
        ----------
        message
            A message describing the error.
        """
        super().__init__(message)
