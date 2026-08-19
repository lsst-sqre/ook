"""Tests for ORCID normalization on the AuthorStore write path."""

from __future__ import annotations

import pytest

from ook.domain.authors import Author
from ook.exceptions import DuplicateOrcidError, InvalidOrcidError
from ook.factory import Factory


@pytest.mark.asyncio
async def test_upsert_normalizes_url_form_orcid(factory: Factory) -> None:
    """A URL-form ORCID is stored bare and found by either spelling."""
    async with factory.db_session.begin():
        store = factory.create_author_store()
        await store.upsert_authors(
            [
                Author(
                    internal_id="sickj",
                    surname="Sick",
                    given_name="Jonathan",
                    orcid="https://orcid.org/0000-0003-3001-676X",
                )
            ],
            git_ref="test",
        )

        stored = await store.get_author_by_id("sickj")
        assert stored is not None
        assert stored.orcid == "0000-0003-3001-676X"

        by_bare = await store.get_author_by_orcid("0000-0003-3001-676X")
        assert by_bare is not None
        assert by_bare.internal_id == "sickj"


@pytest.mark.asyncio
async def test_upsert_uppercases_check_character(factory: Factory) -> None:
    """A lowercase checksum character is stored uppercased."""
    async with factory.db_session.begin():
        store = factory.create_author_store()
        await store.upsert_authors(
            [
                Author(
                    internal_id="sickj",
                    surname="Sick",
                    given_name="Jonathan",
                    orcid="0000-0003-3001-676x",
                )
            ],
            git_ref="test",
        )

        stored = await store.get_author_by_id("sickj")
        assert stored is not None
        assert stored.orcid == "0000-0003-3001-676X"


@pytest.mark.asyncio
async def test_upsert_rejects_bad_check_digit(factory: Factory) -> None:
    """A bad check digit aborts the run before anything is written."""
    async with factory.db_session.begin():
        store = factory.create_author_store()

        with pytest.raises(InvalidOrcidError) as exc_info:
            await store.upsert_authors(
                [
                    Author(
                        internal_id="good",
                        surname="Good",
                        given_name="Greta",
                        orcid="0000-0001-2345-6789",
                    ),
                    Author(
                        internal_id="bad",
                        surname="Bad",
                        given_name="Bart",
                        orcid="0000-0003-3001-6760",
                    ),
                ],
                git_ref="main",
            )

        error = exc_info.value
        assert [a.internal_id for a in error.authors] == ["bad"]
        assert error.git_ref == "main"

        # Nothing was written: the author whose ORCID was fine is absent too.
        assert await store.get_author_by_id("good") is None
        assert await store.get_author_by_id("bad") is None


@pytest.mark.asyncio
async def test_invalid_orcid_error_slack_message(factory: Factory) -> None:
    """The Slack message names the author, the bad value, and the git ref."""
    async with factory.db_session.begin():
        store = factory.create_author_store()

        with pytest.raises(InvalidOrcidError) as exc_info:
            await store.upsert_authors(
                [
                    Author(
                        internal_id="bad",
                        surname="Bad",
                        given_name="Bart",
                        orcid="not-an-orcid",
                    )
                ],
                git_ref="deadbeef",
            )

        message = exc_info.value.to_slack().message
        assert "bad" in message
        assert "not-an-orcid" in message
        assert "deadbeef" in message


@pytest.mark.asyncio
async def test_upsert_reports_every_invalid_orcid(factory: Factory) -> None:
    """All offenders are reported in one error, not just the first."""
    async with factory.db_session.begin():
        store = factory.create_author_store()

        with pytest.raises(InvalidOrcidError) as exc_info:
            await store.upsert_authors(
                [
                    Author(
                        internal_id="bad1",
                        surname="One",
                        given_name=None,
                        orcid="0000-0003-3001-6760",
                    ),
                    Author(
                        internal_id="ok",
                        surname="Fine",
                        given_name=None,
                        orcid="0000-0001-2345-6789",
                    ),
                    Author(
                        internal_id="bad2",
                        surname="Two",
                        given_name=None,
                        orcid="https://example.com/0000-0003-3001-676X",
                    ),
                ],
                git_ref="test",
            )

        error = exc_info.value
        assert [a.internal_id for a in error.authors] == ["bad1", "bad2"]
        message = error.to_slack().message
        assert "bad1" in message
        assert "bad2" in message


@pytest.mark.asyncio
async def test_normalization_precedes_conflict_check(
    factory: Factory,
) -> None:
    """A URL-form ORCID still collides with its stored bare twin.

    The normalization pass has to run before ``_check_orcid_conflicts``,
    which compares incoming ORCIDs to stored ones with plain equality: an
    un-normalized URL-form value would fail to match its stored bare twin
    and the duplicate would go undetected.
    """
    async with factory.db_session.begin():
        store = factory.create_author_store()
        await store.upsert_authors(
            [
                Author(
                    internal_id="sickj",
                    surname="Sick",
                    given_name="Jonathan",
                    orcid="0000-0003-3001-676X",
                )
            ],
            git_ref="test",
        )

        with pytest.raises(DuplicateOrcidError) as exc_info:
            await store.upsert_authors(
                [
                    Author(
                        internal_id="imposter",
                        surname="Sick",
                        given_name="Jon",
                        orcid="https://orcid.org/0000-0003-3001-676x",
                    )
                ],
                git_ref="test",
            )

        error = exc_info.value
        assert error.orcid == "0000-0003-3001-676X"
        assert error.existing_author.internal_id == "sickj"
        assert [a.internal_id for a in error.new_authors] == ["imposter"]


@pytest.mark.asyncio
async def test_upsert_allows_null_orcid(factory: Factory) -> None:
    """An author without an ORCID passes the pre-flight pass untouched."""
    async with factory.db_session.begin():
        store = factory.create_author_store()
        await store.upsert_authors(
            [
                Author(
                    internal_id="noorcid",
                    surname="Nemo",
                    given_name="Nobody",
                    orcid=None,
                )
            ],
            git_ref="test",
        )

        stored = await store.get_author_by_id("noorcid")
        assert stored is not None
        assert stored.orcid is None
