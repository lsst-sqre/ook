"""Tests for AuthorStore.delete_author()."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from ook.dbschema.authors import (
    SqlAffiliation,
    SqlAuthor,
    SqlAuthorAffiliation,
)
from ook.domain.authors import Address, Affiliation, Author
from ook.factory import Factory


@pytest.mark.asyncio
async def test_delete_author_removes_associations(
    factory: Factory,
) -> None:
    """Test that deleting an author removes author-affiliation associations."""
    async with factory.db_session.begin():
        store = factory.create_author_store()

        # Create test affiliation
        test_affiliation = Affiliation(
            internal_id="test_aff",
            name="Test Affiliation",
            address=Address(city="Test City"),
        )
        await store.upsert_affiliations([test_affiliation])

        # Create test author with affiliation
        test_author = Author(
            internal_id="testauthor",
            surname="Test",
            given_name="Author",
            affiliations=[test_affiliation],
        )
        await store.upsert_authors([test_author], git_ref="test")

        # Verify author-affiliation association exists
        stmt = (
            select(SqlAuthorAffiliation)
            .join(SqlAuthor)
            .where(SqlAuthor.internal_id == "testauthor")
        )
        result = await store._session.execute(stmt)
        associations = result.all()
        assert len(associations) > 0

        # Delete the author
        await store.delete_author("testauthor")

        # Verify author is gone
        author = await store.get_author_by_id("testauthor")
        assert author is None

        # Verify author-affiliation associations are gone
        result = await store._session.execute(stmt)
        associations = result.all()
        assert len(associations) == 0


@pytest.mark.asyncio
async def test_delete_author_preserves_affiliations(
    factory: Factory,
) -> None:
    """Test that deleting an author preserves the affiliation itself."""
    async with factory.db_session.begin():
        store = factory.create_author_store()

        # Create test affiliation
        test_affiliation = Affiliation(
            internal_id="test_aff",
            name="Test Affiliation",
            address=Address(city="Test City"),
        )
        await store.upsert_affiliations([test_affiliation])

        # Create test author with affiliation
        test_author = Author(
            internal_id="testauthor",
            surname="Test",
            given_name="Author",
            affiliations=[test_affiliation],
        )
        await store.upsert_authors([test_author], git_ref="test")

        # Delete the author
        await store.delete_author("testauthor")

        # Verify affiliation still exists
        stmt = select(SqlAffiliation).where(
            SqlAffiliation.internal_id == "test_aff"
        )
        result = await store._session.execute(stmt)
        affiliation = result.scalar_one_or_none()
        assert affiliation is not None
        assert affiliation.name == "Test Affiliation"
