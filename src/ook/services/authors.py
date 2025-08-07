"""Author information service."""

from __future__ import annotations

from typing import Any

from safir.database import CountedPaginatedList
from structlog.stdlib import BoundLogger

from ook.domain.authors import Author, AuthorSearchResult
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
