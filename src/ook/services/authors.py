"""Author information service."""

from __future__ import annotations

from typing import Any

from safir.database import CountedPaginatedList
from structlog.stdlib import BoundLogger

from ook.domain.authors import Author, AuthorAlias, AuthorSearchResult
from ook.storage.authorstore import (
    AuthorsCursor,
    AuthorSearchCursor,
    AuthorStore,
)


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

    async def search_authors(
        self,
        *,
        search_query: str,
        limit: int | None = None,
        cursor: AuthorSearchCursor | None = None,
    ) -> CountedPaginatedList[AuthorSearchResult, AuthorSearchCursor]:
        """Search authors with fuzzy matching and relevance scoring.

        Parameters
        ----------
        search_query
            The search query string to match against author names.
        limit
            The maximum number of authors to return. If None, all matching
            authors are returned.
        cursor
            The cursor for pagination. If None, the first page is returned.

        Returns
        -------
        CountedPaginatedList
            A paginated list of author search results with relevance scores.
        """
        return await self._author_store.search_authors(
            search_query=search_query, limit=limit, cursor=cursor
        )

    async def delete_author(self, internal_id: str) -> None:
        """Delete an author by their internal ID.

        This operation permanently removes the author and all their
        affiliation associations from the database.

        Parameters
        ----------
        internal_id
            The internal ID of the author to delete.
        """
        await self._author_store.delete_author(internal_id)

        self._logger.info(
            "Author deleted from database",
            internal_id=internal_id,
        )

    async def get_author_aliases(self) -> list[AuthorAlias]:
        """Get all author internal ID aliases.

        Returns
        -------
        list of AuthorAlias
            All aliases, sorted by the alias internal ID.
        """
        return await self._author_store.get_author_aliases()

    async def get_author_alias(self, internal_id: str) -> AuthorAlias | None:
        """Get an author internal ID alias.

        Parameters
        ----------
        internal_id
            The alias internal ID.

        Returns
        -------
        AuthorAlias or None
            The alias, or None if no such alias exists.
        """
        return await self._author_store.get_author_alias(internal_id)

    async def create_author_alias(
        self, *, internal_id: str, author_internal_id: str
    ) -> AuthorAlias:
        """Create an author internal ID alias that resolves to a root author.

        If an author record already exists with the alias internal ID, it is
        merged into the root author: contributor records pointing to it are
        re-pointed to the root author and the record is deleted.

        Parameters
        ----------
        internal_id
            The alias internal ID.
        author_internal_id
            The internal ID of the root author the alias resolves to.

        Returns
        -------
        AuthorAlias
            The created alias.
        """
        alias = await self._author_store.create_author_alias(
            internal_id=internal_id,
            author_internal_id=author_internal_id,
        )

        self._logger.info(
            "Created author alias",
            internal_id=internal_id,
            author_internal_id=author_internal_id,
        )

        return alias

    async def delete_author_alias(self, internal_id: str) -> bool:
        """Delete an author internal ID alias.

        Parameters
        ----------
        internal_id
            The alias internal ID.

        Returns
        -------
        bool
            True if an alias was deleted, False if no such alias exists.
        """
        deleted = await self._author_store.delete_author_alias(internal_id)

        if deleted:
            self._logger.info(
                "Author alias deleted from database",
                internal_id=internal_id,
            )

        return deleted

    async def migrate_country_codes(
        self, *, dry_run: bool = False
    ) -> dict[str, Any]:
        """Migrate all country codes from existing country names.

        This service method orchestrates the country code migration process,
        including logging and result processing. It delegates the actual
        database operations to the AuthorStore.

        Parameters
        ----------
        dry_run
            If True, show what would be updated without making changes.

        Returns
        -------
        dict[str, Any]
            A dictionary containing migration results with keys:
            - updated: int - Number of records successfully updated
            - failed: int - Number of records that failed to convert
            - total: int - Total number of records processed
            - failed_conversions: list[dict] - Details of failed conversions
            - conversions: dict[int, dict] - Details of successful
              conversions (dry run only)
        """
        self._logger.info("Starting country code migration", dry_run=dry_run)

        # Delegate to the store for the actual migration logic
        result = await self._author_store.migrate_country_codes(
            dry_run=dry_run
        )
        if result["total"] == 0:
            self._logger.info("No records to update")
            return result
        self._logger.info(
            "Country code migration completed",
            updated=result["updated"],
            failed=result["failed"],
            total=result["total"],
            dry_run=dry_run,
        )

        if dry_run and result["conversions"]:
            example_count = min(5, len(result["conversions"]))
            for i, (affiliation_id, conversion) in enumerate(
                result["conversions"].items()
            ):
                if i >= example_count:
                    break
                self._logger.info(
                    "Would convert",
                    affiliation_id=affiliation_id,
                    **conversion,
                )
            if len(result["conversions"]) > example_count:
                remaining = len(result["conversions"]) - example_count
                self._logger.info(f"... and {remaining} more conversions")

        if result["failed"] > 0:
            self._logger.warning(
                "Some country names could not be converted",
                failed_count=result["failed"],
            )
            unique_failed_names = {
                fc["country_name"] for fc in result["failed_conversions"]
            }
            for country_name in sorted(unique_failed_names):
                self._logger.warning(
                    "Failed to convert country name",
                    country_name=country_name,
                )

        return result
