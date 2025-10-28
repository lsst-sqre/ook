"""Tests for ORCID conflict detection in AuthorStore."""

from __future__ import annotations

import pytest

from ook.domain.authors import Author
from ook.exceptions import DuplicateOrcidError
from ook.factory import Factory


@pytest.mark.asyncio
async def test_orcid_conflict_raises_error(
    factory: Factory,
) -> None:
    """Test that duplicate ORCID with different internal_id raises error."""
    async with factory.db_session.begin():
        store = factory.create_author_store()

        # Create first author with an ORCID
        author1 = Author(
            internal_id="author1",
            surname="Smith",
            given_name="John",
            orcid="0000-0001-2345-6789",
        )
        await store.upsert_authors([author1], git_ref="test")

        # Attempt to create second author with same ORCID but different ID
        author2 = Author(
            internal_id="author2",
            surname="Smith",
            given_name="Jane",
            orcid="0000-0001-2345-6789",  # Same ORCID
        )

        # This should raise DuplicateOrcidError
        with pytest.raises(DuplicateOrcidError) as exc_info:
            await store.upsert_authors([author2], git_ref="test")

        # Verify error details
        error = exc_info.value
        assert error.orcid == "0000-0001-2345-6789"
        assert error.existing_author.internal_id == "author1"
        assert len(error.new_authors) == 1
        assert error.new_authors[0].internal_id == "author2"
        assert error.git_ref == "test"


@pytest.mark.asyncio
async def test_orcid_no_conflict_same_author(
    factory: Factory,
) -> None:
    """Test that updating same author with same ORCID does not raise error."""
    async with factory.db_session.begin():
        store = factory.create_author_store()

        # Create author with ORCID
        author = Author(
            internal_id="author1",
            surname="Smith",
            given_name="John",
            orcid="0000-0001-2345-6789",
        )
        await store.upsert_authors([author], git_ref="test")

        # Update same author (same internal_id) with same ORCID
        # This should not raise an error
        author_updated = Author(
            internal_id="author1",  # Same internal_id
            surname="Smith",
            given_name="Jonathan",  # Updated name
            orcid="0000-0001-2345-6789",  # Same ORCID
        )
        await store.upsert_authors([author_updated], git_ref="test")

        # Verify author was updated
        retrieved_author = await store.get_author_by_id("author1")
        assert retrieved_author is not None
        assert retrieved_author.given_name == "Jonathan"
        assert retrieved_author.orcid == "0000-0001-2345-6789"


@pytest.mark.asyncio
async def test_orcid_no_conflict_different_orcids(
    factory: Factory,
) -> None:
    """Test that different authors with different ORCIDs work fine."""
    async with factory.db_session.begin():
        store = factory.create_author_store()

        # Create multiple authors with different ORCIDs
        authors = [
            Author(
                internal_id="author1",
                surname="Smith",
                given_name="John",
                orcid="0000-0001-2345-6789",
            ),
            Author(
                internal_id="author2",
                surname="Doe",
                given_name="Jane",
                orcid="0000-0002-3456-7890",
            ),
            Author(
                internal_id="author3",
                surname="Johnson",
                given_name="Bob",
                orcid="0000-0003-4567-8901",
            ),
        ]

        # This should succeed without any conflicts
        await store.upsert_authors(authors, git_ref="test")

        # Verify all authors were created
        for author in authors:
            retrieved = await store.get_author_by_id(author.internal_id)
            assert retrieved is not None
            assert retrieved.orcid == author.orcid


@pytest.mark.asyncio
async def test_orcid_conflict_with_multiple_new_authors(
    factory: Factory,
) -> None:
    """Test ORCID conflict when multiple new authors share same ORCID."""
    async with factory.db_session.begin():
        store = factory.create_author_store()

        # Create existing author with ORCID
        existing = Author(
            internal_id="existing",
            surname="Smith",
            given_name="John",
            orcid="0000-0001-2345-6789",
        )
        await store.upsert_authors([existing], git_ref="test")

        # Attempt to create multiple new authors with same ORCID
        new_authors = [
            Author(
                internal_id="new1",
                surname="Smith",
                given_name="Jane",
                orcid="0000-0001-2345-6789",
            ),
            Author(
                internal_id="new2",
                surname="Smith",
                given_name="Bob",
                orcid="0000-0001-2345-6789",
            ),
        ]

        # This should raise DuplicateOrcidError
        with pytest.raises(DuplicateOrcidError) as exc_info:
            await store.upsert_authors(new_authors, git_ref="test")

        # Verify error includes all new authors attempting to use the ORCID
        error = exc_info.value
        assert error.orcid == "0000-0001-2345-6789"
        assert error.existing_author.internal_id == "existing"
        assert len(error.new_authors) >= 1  # At least one conflicting author
