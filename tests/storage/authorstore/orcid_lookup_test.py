"""Tests for AuthorStore.get_author_by_orcid()."""

from __future__ import annotations

import pytest
from sqlalchemy import Select, text
from sqlalchemy.dialects import postgresql

from ook.domain.authors import Address, Affiliation, Author
from ook.factory import Factory
from ook.storage.authorstore._query import create_author_by_orcid_stmt


@pytest.mark.asyncio
async def test_get_author_by_orcid_hit(factory: Factory) -> None:
    async with factory.db_session.begin():
        store = factory.create_author_store()

        affiliation = Affiliation(
            internal_id="test_aff",
            name="Test Affiliation",
            address=Address(city="Test City"),
        )
        await store.upsert_affiliations([affiliation])
        await store.upsert_authors(
            [
                Author(
                    internal_id="sickj",
                    surname="Sick",
                    given_name="Jonathan",
                    orcid="0000-0003-3001-676X",
                    affiliations=[affiliation],
                )
            ],
            git_ref="test",
        )

        author = await store.get_author_by_orcid("0000-0003-3001-676X")

        assert author is not None
        assert author.internal_id == "sickj"
        assert author.orcid == "0000-0003-3001-676X"
        # The ORCID path returns the same shape as the internal-ID path.
        assert author == await store.get_author_by_id("sickj")
        assert [a.internal_id for a in author.affiliations] == ["test_aff"]


@pytest.mark.asyncio
async def test_get_author_by_orcid_miss(factory: Factory) -> None:
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

        assert await store.get_author_by_orcid("0000-0001-2345-6789") is None


@pytest.mark.asyncio
async def test_get_author_by_orcid_skips_null_orcid(
    factory: Factory,
) -> None:
    """An author without an ORCID is never the answer to an ORCID lookup."""
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

        assert await store.get_author_by_orcid("0000-0003-3001-676X") is None
        assert await store.get_author_by_id("noorcid") is not None


@pytest.mark.asyncio
async def test_author_by_orcid_stmt_uses_unique_index(
    factory: Factory,
) -> None:
    """The ORCID predicate can ride the ``uq_author_orcid`` unique index.

    Postgres picks a sequential scan on a table this small no matter how the
    predicate is written, so sequential scans are disabled for the plan: what
    is under test is that the index is *usable*, which is what wrapping either
    side of the comparison in ``upper()`` would destroy.
    """
    async with factory.db_session.begin():
        session = factory.db_session
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        stmt = create_author_by_orcid_stmt("0000-0003-3001-676X")
        result = await session.execute(text("EXPLAIN " + _compile(stmt)))
        plan = "\n".join(row[0] for row in result)

        assert "uq_author_orcid" in plan
        assert "Seq Scan on author" not in plan


def _compile(stmt: Select) -> str:
    """Render a statement as literal SQL for ``EXPLAIN``."""
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
