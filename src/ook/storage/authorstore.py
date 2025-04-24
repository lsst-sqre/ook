"""Storage interface for author information."""

from __future__ import annotations

from collections.abc import Sequence

from safir.datetime import current_datetime
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_scoped_session
from structlog.stdlib import BoundLogger

from ook.dbschema.authors import (
    SqlAffiliation,
    SqlAuthor,
    SqlAuthorAffiliation,
    SqlCollaboration,
)
from ook.domain.authors import Affiliation, Author, Collaboration

__all__ = ["AuthorStore"]


class AuthorStore:
    """Interface for storing author information in a database."""

    def __init__(
        self, session: async_scoped_session, logger: BoundLogger
    ) -> None:
        self._session = session
        self._logger = logger

    async def upsert_affiliations(
        self, affiliations: Sequence[Affiliation]
    ) -> None:
        """Upsert affiliations into the database.

        Parameters
        ----------
        affiliations
            A sequence of Affiliation domain models to be upserted into the
            database.
        """
        now = current_datetime(microseconds=False)

        affiliation_data = [
            {
                "internal_id": a.internal_id,
                "name": a.name,
                "address": a.address,
                "date_updated": now,
            }
            for a in affiliations
        ]
        insert_stmt = pg_insert(SqlAffiliation).values(affiliation_data)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=["internal_id"],
            set_={
                "name": insert_stmt.excluded.name,
                "address": insert_stmt.excluded.address,
                "date_updated": insert_stmt.excluded.date_updated,
            },
        )
        await self._session.execute(upsert_stmt)
        await self._session.flush()

    async def upsert_authors(self, authors: Sequence[Author]) -> None:
        """Upsert authors into the database.

        Parameters
        ----------
        authors
            A sequence of Author domain models to be upserted into the
            database.
        """
        now = current_datetime(microseconds=False)

        author_data = [
            {
                "internal_id": a.internal_id,
                "surname": a.surname,
                "given_name": a.given_name,
                "notes": a.notes,
                "email": a.email,
                "orcid": a.orcid,
                "date_updated": now,
            }
            for a in authors
        ]
        insert_stmt = pg_insert(SqlAuthor).values(author_data)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=["internal_id"],
            set_={
                "internal_id": insert_stmt.excluded.internal_id,
                "surname": insert_stmt.excluded.surname,
                "given_name": insert_stmt.excluded.given_name,
                "notes": insert_stmt.excluded.notes,
                "email": insert_stmt.excluded.email,
                "orcid": insert_stmt.excluded.orcid,
                "date_updated": now,
            },
        )
        await self._session.execute(upsert_stmt)
        await self._session.flush()

        # Handle author-affiliation relationships
        for author in authors:
            if not author.affiliations:
                continue

            # Get the author ID
            author_query = select(SqlAuthor.id).where(
                SqlAuthor.internal_id == author.internal_id
            )
            author_id_result = await self._session.execute(author_query)
            author_id = author_id_result.scalar_one()

            # Get the affiliation IDs
            affiliation_internal_ids = [
                aff.internal_id for aff in author.affiliations
            ]
            if not affiliation_internal_ids:
                continue

            affiliation_query = select(
                SqlAffiliation.id, SqlAffiliation.internal_id
            ).where(SqlAffiliation.internal_id.in_(affiliation_internal_ids))
            affiliation_results = await self._session.execute(
                affiliation_query
            )
            affiliation_map = {
                internal_id: id for id, internal_id in affiliation_results
            }

            # Delete existing author-affiliation relationships for this author
            delete_stmt = delete(SqlAuthorAffiliation).where(
                SqlAuthorAffiliation.author_id == author_id
            )
            await self._session.execute(delete_stmt)

            # Create new author-affiliation relationships
            author_affiliation_data = [
                {
                    "author_id": author_id,
                    "affiliation_id": affiliation_map.get(aff.internal_id),
                    "position": i,
                }
                for i, aff in enumerate(author.affiliations)
                if affiliation_map.get(aff.internal_id) is not None
            ]

            if author_affiliation_data:
                await self._session.execute(
                    pg_insert(SqlAuthorAffiliation).values(
                        author_affiliation_data
                    )
                )

        await self._session.flush()

    async def upsert_collaborations(
        self, collaborations: Sequence[Collaboration]
    ) -> None:
        """Upsert collaborations into the database.

        Parameters
        ----------
        collaborations
            A sequence of Collaboration domain models to be upserted into the
            database.
        """
        now = current_datetime(microseconds=False)

        collaboration_data = [
            {
                "internal_id": c.internal_id,
                "name": c.name,
                "date_updated": now,
            }
            for c in collaborations
        ]
        insert_stmt = pg_insert(SqlCollaboration).values(collaboration_data)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=["internal_id"],
            set_={
                "name": insert_stmt.excluded.name,
                "date_updated": insert_stmt.excluded.date_updated,
            },
        )
        await self._session.execute(upsert_stmt)
        await self._session.flush()
