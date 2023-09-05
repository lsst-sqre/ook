"""Ook's exceptions."""

from __future__ import annotations

__all__ = ["LtdSlugClassificationError"]


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
