"""Author information service."""

from __future__ import annotations

from safir.database import CountedPaginatedList
from structlog.stdlib import BoundLogger

from ook.domain.authors import Author
from ook.storage.authorstore import AuthorsCursor, AuthorStore


class AuthorService:
    """Service for managing author information.

    Parameters
    ----------
    author_store
        The author store, which interfaces with the database.
    loggger
        The logger.
    """

    def __init__(
        self, *, author_store: AuthorStore, loggger: BoundLogger
    ) -> None:
        self._author_store = author_store
        self._logger = loggger

    async def get_author_by_id(self, internal_id: str) -> Author | None:
        """Get an author by their internal ID.

        Parameters
        ----------
        internal_id
            The internal ID of the author to retrieve.

        Returns
        -------
        Author or None
            The author with the specified internal ID, or None if not found.
        """
        return await self._author_store.get_author_by_id(internal_id)

    async def get_authors(
        self, *, limit: int | None = None, cursor: AuthorsCursor | None
    ) -> CountedPaginatedList[Author, AuthorsCursor]:
        """Get all authors with optional pagination.

        Parameters
        ----------
        limit
            The maximum number of authors to return. If None, all authors are
            returned.
        cursor
            The cursor for pagination. If None, the first page is returned.

        Returns
        -------
        CountedPaginatedList
            A paginated list of authors.
        """
        return await self._author_store.get_authors(limit=limit, cursor=cursor)
