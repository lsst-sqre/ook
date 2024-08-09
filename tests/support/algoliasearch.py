"""Mock the Algolia API client."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Self
from unittest.mock import patch


class MockSearchClient:
    """A mock Algolia SearchClient for testing."""

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    @classmethod
    async def create(cls, *args: Any, **kwargs: Any) -> Self:
        """Create a mock SearchClient."""
        return cls(*args, **kwargs)

    async def close_async(self) -> None:
        """Close the client."""

    def init_index(self, index_name: str) -> MockAlgoliaIndex:
        """Initialize an index."""
        return MockAlgoliaIndex(index_name)


class MockAlgoliaIndex:
    """A mock Algolia index for testing."""

    def __init__(self, index_name: str) -> None: ...

    async def save_objects_async(self, objects: list[dict[str, Any]]) -> None:
        """Save objects to the index."""

    async def delete_objects_async(self, object_ids: list[str]) -> None:
        """Delete objects from the index."""

    def browse_objects(self, settings: dict[str, Any]) -> list[dict[str, Any]]:
        """Browse the index."""
        return []


def patch_algoliasearch() -> Iterator[MockSearchClient]:
    """Patch the Algolia API client."""
    with patch("algoliasearch.search_client.SearchClient") as mock:
        yield mock.return_value
