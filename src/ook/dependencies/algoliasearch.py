"""Dependency for managing an Algolia search index."""

from __future__ import annotations

from algoliasearch.search_client import SearchClient

from ..config import config


class AlgoliaSearchDependency:
    """Provides an Algolia SearchClient as a FastAPI dependency."""

    def __init__(self) -> None:
        self._search_client: SearchClient | None = None

    async def __call__(self) -> SearchClient:
        """Return SearchClient."""
        if self._search_client is None:
            if config.algolia_app_id is None or config.algolia_api_key is None:
                raise RuntimeError(
                    "Algolia app ID and API key must be set to use this "
                    "service."
                )
            self._search_client = await SearchClient.create(
                config.algolia_app_id,
                config.algolia_api_key.get_secret_value(),
            ).__aenter__()
        return self._search_client

    async def close(self) -> None:
        """Close the Algolia SearchClient."""
        if self._search_client is not None:
            await self._search_client.close_async()


algolia_client_dependency = AlgoliaSearchDependency()
"""The dependency that will return the logger for the current request."""
