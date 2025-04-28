"""Glossary service."""

from __future__ import annotations

from structlog.stdlib import BoundLogger

from ook.domain.glossary import GlossaryTerm
from ook.storage.glossarystore import GlossaryStore

__all__ = ["GlossaryService"]


class GlossaryService:
    """Service for accessing glossary terms.

    Parameters
    ----------
    glossary_store
        The glossary store, which interfaces with the database.
    loggger
        The logger.
    """

    def __init__(
        self, *, glossary_store: GlossaryStore, logger: BoundLogger
    ) -> None:
        self._glossary_store = glossary_store
        self._logger = logger

    async def search(
        self,
        *,
        search_term: str,
        include_abbr: bool = True,
        include_terms: bool = True,
        search_definitions: bool = True,
        contexts: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[GlossaryTerm]:
        """Search the glossary for terms matching the search term.

        Parameters
        ----------
        search_term
            The search term to match against the glossary terms.
        include_abbr
            Whether to include abbreviations in the search.
        include_terms
            Whether to include terms in the search.
        search_definitions
            Whether to search definitions as well.
        contexts
            The contexts to search in. If not provided, all contexts
            are searched.
        limit
            The maximum number of results to return.
        offset
            The number of results to skip.

        Returns
        -------
        list[GlossaryTerm]
            A list of glossary terms matching the search term.
        """
        return await self._glossary_store.search_terms(
            search_term=search_term,
            include_abbr=include_abbr,
            include_terms=include_terms,
            search_definitions=search_definitions,
            contexts=contexts,
            limit=limit,
            offset=offset,
        )
