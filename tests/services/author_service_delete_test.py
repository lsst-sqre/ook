"""Tests for AuthorService.delete_author()."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ook.factory import Factory


@pytest.mark.asyncio
async def test_delete_author_delegates_to_store(
    factory: Factory,
) -> None:
    """Test that AuthorService.delete_author delegates to the store."""
    author_service = factory.create_author_service()

    # Mock the store method
    with patch.object(
        author_service._author_store,
        "delete_author",
        new_callable=AsyncMock,
    ) as mock_delete:
        await author_service.delete_author("sickj")

        # Verify store method was called with correct parameters
        mock_delete.assert_called_once_with("sickj")
