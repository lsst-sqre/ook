"""Tests for author internal ID aliases in AuthorStore."""

from __future__ import annotations

import pytest

from ook.domain.authors import Author
from ook.exceptions import ConflictError, NotFoundError
from ook.factory import Factory


@pytest.mark.asyncio
async def test_alias_resolves_to_root_author(factory: Factory) -> None:
    """Test that get_author_by_id resolves an alias to the root author."""
    async with factory.db_session.begin():
        store = factory.create_author_store()

        author = Author(
            internal_id="author1",
            surname="Smith",
            given_name="John",
            orcid="0000-0001-2345-6789",
        )
        await store.upsert_authors([author], git_ref="test")

        alias = await store.create_author_alias(
            internal_id="author1old", author_internal_id="author1"
        )
        assert alias.internal_id == "author1old"
        assert alias.author_internal_id == "author1"

        # The alias resolves to the root author's record
        resolved = await store.get_author_by_id("author1old")
        assert resolved is not None
        assert resolved.internal_id == "author1"
        assert resolved.orcid == "0000-0001-2345-6789"

        # The root author is still accessible directly
        root = await store.get_author_by_id("author1")
        assert root is not None
        assert root.internal_id == "author1"

        # The alias is retrievable
        retrieved_alias = await store.get_author_alias("author1old")
        assert retrieved_alias is not None
        assert retrieved_alias.author_internal_id == "author1"

        aliases = await store.get_author_aliases()
        assert [a.internal_id for a in aliases] == ["author1old"]

        # The alias doesn't appear in the author listing
        authors_page = await store.get_authors(limit=None)
        internal_ids = [a.internal_id for a in authors_page.entries]
        assert "author1old" not in internal_ids
        assert "author1" in internal_ids


@pytest.mark.asyncio
async def test_create_alias_idempotent(factory: Factory) -> None:
    """Test that re-creating an identical alias succeeds."""
    async with factory.db_session.begin():
        store = factory.create_author_store()

        author = Author(
            internal_id="author1", surname="Smith", given_name="John"
        )
        await store.upsert_authors([author], git_ref="test")

        await store.create_author_alias(
            internal_id="author1old", author_internal_id="author1"
        )
        alias = await store.create_author_alias(
            internal_id="author1old", author_internal_id="author1"
        )
        assert alias.internal_id == "author1old"
        assert alias.author_internal_id == "author1"

        aliases = await store.get_author_aliases()
        assert len(aliases) == 1


@pytest.mark.asyncio
async def test_create_alias_conflicts(factory: Factory) -> None:
    """Test conflict and not-found errors when creating aliases."""
    async with factory.db_session.begin():
        store = factory.create_author_store()

        authors = [
            Author(internal_id="author1", surname="Smith", given_name="John"),
            Author(internal_id="author2", surname="Doe", given_name="Jane"),
        ]
        await store.upsert_authors(authors, git_ref="test")

        # The root author must exist
        with pytest.raises(NotFoundError):
            await store.create_author_alias(
                internal_id="alias1", author_internal_id="doesnotexist"
            )

        # An alias can't be the same as the root author's internal ID
        with pytest.raises(ConflictError):
            await store.create_author_alias(
                internal_id="author1", author_internal_id="author1"
            )

        # An alias can't be re-pointed to a different author
        await store.create_author_alias(
            internal_id="alias1", author_internal_id="author1"
        )
        with pytest.raises(ConflictError):
            await store.create_author_alias(
                internal_id="alias1", author_internal_id="author2"
            )


@pytest.mark.asyncio
async def test_create_alias_merges_existing_author(factory: Factory) -> None:
    """Test that creating an alias for an existing author ID merges that
    author record into the root author.
    """
    async with factory.db_session.begin():
        store = factory.create_author_store()

        authors = [
            Author(
                internal_id="author1",
                surname="Marshall",
                given_name="Phil",
                orcid="0000-0001-2345-6789",
            ),
            Author(
                internal_id="author1dup",
                surname="Marshall",
                given_name="Philip J.",
            ),
        ]
        await store.upsert_authors(authors, git_ref="test")

        await store.create_author_alias(
            internal_id="author1dup", author_internal_id="author1"
        )

        # The duplicate author record is gone; the ID resolves to the root
        resolved = await store.get_author_by_id("author1dup")
        assert resolved is not None
        assert resolved.internal_id == "author1"
        assert resolved.given_name == "Phil"

        authors_page = await store.get_authors(limit=None)
        internal_ids = [a.internal_id for a in authors_page.entries]
        assert "author1dup" not in internal_ids


@pytest.mark.asyncio
async def test_upsert_authors_skips_aliased_ids(factory: Factory) -> None:
    """Test that upserting an author whose internal ID is a registered alias
    is skipped, even when it would otherwise raise a duplicate ORCID error.
    """
    async with factory.db_session.begin():
        store = factory.create_author_store()

        root = Author(
            internal_id="author1",
            surname="Marshall",
            given_name="Phil",
            orcid="0000-0001-2345-6789",
        )
        await store.upsert_authors([root], git_ref="test")
        await store.create_author_alias(
            internal_id="author1dup", author_internal_id="author1"
        )

        # Simulates an authordb.yaml sync where both IDs carry the same ORCID
        incoming = [
            Author(
                internal_id="author1",
                surname="Marshall",
                given_name="Phil",
                orcid="0000-0001-2345-6789",
            ),
            Author(
                internal_id="author1dup",
                surname="Marshall",
                given_name="Philip J.",
                orcid="0000-0001-2345-6789",
            ),
        ]
        await store.upsert_authors(incoming, git_ref="test")

        # The aliased ID wasn't recreated as its own author record
        authors_page = await store.get_authors(limit=None)
        internal_ids = [a.internal_id for a in authors_page.entries]
        assert internal_ids == ["author1"]


@pytest.mark.asyncio
async def test_delete_author_alias(factory: Factory) -> None:
    """Test deleting an alias."""
    async with factory.db_session.begin():
        store = factory.create_author_store()

        author = Author(
            internal_id="author1", surname="Smith", given_name="John"
        )
        await store.upsert_authors([author], git_ref="test")
        await store.create_author_alias(
            internal_id="author1old", author_internal_id="author1"
        )

        assert await store.delete_author_alias("author1old") is True
        assert await store.get_author_alias("author1old") is None
        assert await store.get_author_by_id("author1old") is None

        # Deleting a nonexistent alias reports False
        assert await store.delete_author_alias("author1old") is False


@pytest.mark.asyncio
async def test_delete_author_cascades_aliases(factory: Factory) -> None:
    """Test that deleting a root author also removes its aliases."""
    async with factory.db_session.begin():
        store = factory.create_author_store()

        author = Author(
            internal_id="author1", surname="Smith", given_name="John"
        )
        await store.upsert_authors([author], git_ref="test")
        await store.create_author_alias(
            internal_id="author1old", author_internal_id="author1"
        )

        await store.delete_author("author1")

        assert await store.get_author_by_id("author1") is None
        assert await store.get_author_alias("author1old") is None
