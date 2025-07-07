"""Storage interface for author information."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self, override

from safir.database import (
    CountedPaginatedList,
    CountedPaginatedQueryRunner,
    PaginationCursor,
)
from safir.datetime import current_datetime
from sqlalchemy import Select, case, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB
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

    async def get_author_by_id(self, internal_id: str) -> Author | None:
        """Get an author by their internal ID.

        Parameters
        ----------
        internal_id
            The internal ID of the author to retrieve.

        Returns
        -------
        Author or None
            The author with the specified internal ID, or None if not found.
        """
        # Subquery to get affiliations as a JSON array
        affiliations_subquery = (
            select(
                func.json_agg(
                    func.json_build_object(
                        "internal_id",
                        SqlAffiliation.internal_id,
                        "name",
                        SqlAffiliation.name,
                        "department",
                        SqlAffiliation.department,
                        "email",
                        SqlAffiliation.email_domain,
                        "ror_id",
                        SqlAffiliation.ror_id,
                        "address",
                        case(
                            (
                                func.coalesce(
                                    SqlAffiliation.address_street,
                                    SqlAffiliation.address_city,
                                    SqlAffiliation.address_state,
                                    SqlAffiliation.address_postal_code,
                                    SqlAffiliation.address_country,
                                ).is_(None),
                                None,
                            ),
                            else_=func.json_build_object(
                                "street",
                                SqlAffiliation.address_street,
                                "city",
                                SqlAffiliation.address_city,
                                "state",
                                SqlAffiliation.address_state,
                                "postal_code",
                                SqlAffiliation.address_postal_code,
                                "country",
                                SqlAffiliation.address_country,
                            ),
                        ),
                    ).cast(JSONB)
                ).label("affiliations")
            )
            .select_from(SqlAffiliation)
            .join(
                SqlAuthorAffiliation,
                SqlAffiliation.id == SqlAuthorAffiliation.affiliation_id,
            )
            .join(SqlAuthor, SqlAuthor.id == SqlAuthorAffiliation.author_id)
            .where(SqlAuthor.internal_id == internal_id)
            .group_by(SqlAuthor.id)
            .order_by(
                # Order within json_agg
                func.array_agg(SqlAuthorAffiliation.position)
            )
        ).scalar_subquery()

        # Main query to get author with affiliations
        stmt = select(
            SqlAuthor.internal_id,
            SqlAuthor.surname,
            SqlAuthor.given_name,
            SqlAuthor.orcid,
            SqlAuthor.email,
            SqlAuthor.notes,
            case(
                (affiliations_subquery.is_(None), func.json_build_array()),
                else_=affiliations_subquery,
            ).label("affiliations"),
        ).where(SqlAuthor.internal_id == internal_id)

        result = (await self._session.execute(stmt)).first()

        return (
            Author.model_validate(result, from_attributes=True)
            if result
            else None
        )

    async def get_authors(
        self,
        cursor: AuthorsCursor | None = None,
        limit: int | None = None,
    ) -> CountedPaginatedList[Author, AuthorsCursor]:
        """Get a list of authors with pagination.

        Parameters
        ----------
        cursor
            The pagination cursor for the query.
        limit
            The maximum number of authors to return. If None, all authors
            are returned.

        Returns
        -------
        CountedPaginatedList[Author]
            A paginated list of authors.
        """
        # Subquery to get affiliations as a JSON array for each author
        affiliations_subquery = (
            select(
                SqlAuthor.id.label("author_id"),
                func.json_agg(
                    func.json_build_object(
                        "internal_id",
                        SqlAffiliation.internal_id,
                        "name",
                        SqlAffiliation.name,
                        "department",
                        SqlAffiliation.department,
                        "email",
                        SqlAffiliation.email_domain,
                        "ror_id",
                        SqlAffiliation.ror_id,
                        "address",
                        case(
                            (
                                func.coalesce(
                                    SqlAffiliation.address_street,
                                    SqlAffiliation.address_city,
                                    SqlAffiliation.address_state,
                                    SqlAffiliation.address_postal_code,
                                    SqlAffiliation.address_country,
                                ).is_(None),
                                None,
                            ),
                            else_=func.json_build_object(
                                "street",
                                SqlAffiliation.address_street,
                                "city",
                                SqlAffiliation.address_city,
                                "state",
                                SqlAffiliation.address_state,
                                "postal_code",
                                SqlAffiliation.address_postal_code,
                                "country",
                                SqlAffiliation.address_country,
                            ),
                        ),
                    ).cast(JSONB)
                ).label("affiliations"),
            )
            .select_from(SqlAuthor)
            .join(
                SqlAuthorAffiliation,
                SqlAuthor.id == SqlAuthorAffiliation.author_id,
                isouter=True,
            )
            .join(
                SqlAffiliation,
                SqlAffiliation.id == SqlAuthorAffiliation.affiliation_id,
                isouter=True,
            )
            .group_by(SqlAuthor.id)
            .order_by(
                SqlAuthor.id, func.array_agg(SqlAuthorAffiliation.position)
            )
        ).alias("affiliations_subquery")

        # Main query to get all authors with their affiliations
        stmt = (
            select(
                SqlAuthor.internal_id,
                SqlAuthor.surname,
                SqlAuthor.given_name,
                SqlAuthor.orcid,
                SqlAuthor.email,
                SqlAuthor.notes,
                case(
                    (
                        affiliations_subquery.c.affiliations.is_(None),
                        func.json_build_array(),
                    ),
                    else_=affiliations_subquery.c.affiliations,
                ).label("affiliations"),
            )
            .select_from(SqlAuthor)
            .join(
                affiliations_subquery,
                SqlAuthor.id == affiliations_subquery.c.author_id,
                isouter=True,
            )
        )

        runner = CountedPaginatedQueryRunner(
            entry_type=Author,
            cursor_type=AuthorsCursor,
        )
        return await runner.query_row(
            session=self._session,
            stmt=stmt,
            cursor=cursor,
            limit=limit,
        )

    async def upsert_affiliations(
        self,
        affiliations: Sequence[Affiliation],
        *,
        delete_stale_records: bool = False,
    ) -> None:
        """Upsert affiliations into the database.

        Parameters
        ----------
        affiliations
            A sequence of Affiliation domain models to be upserted into the
            database.
        delete_stale_records
            If True, delete affiliations that are not in the provided list.
        """
        now = current_datetime(microseconds=False)

        affiliation_data = [
            {
                "internal_id": a.internal_id,
                "name": a.name,
                "department": a.department,
                "email_domain": a.email,
                "ror_id": a.ror_id,
                "address_street": a.address.street if a.address else None,
                "address_city": a.address.city if a.address else None,
                "address_state": a.address.state if a.address else None,
                "address_postal_code": (
                    a.address.postal_code if a.address else None
                ),
                "address_country": a.address.country if a.address else None,
                "date_updated": now,
            }
            for a in affiliations
        ]
        insert_stmt = pg_insert(SqlAffiliation).values(affiliation_data)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=["internal_id"],
            set_={
                "name": insert_stmt.excluded.name,
                "department": insert_stmt.excluded.department,
                "email_domain": insert_stmt.excluded.email_domain,
                "ror_id": insert_stmt.excluded.ror_id,
                "address_street": insert_stmt.excluded.address_street,
                "address_city": insert_stmt.excluded.address_city,
                "address_state": insert_stmt.excluded.address_state,
                "address_postal_code": (
                    insert_stmt.excluded.address_postal_code
                ),
                "address_country": insert_stmt.excluded.address_country,
                "date_updated": insert_stmt.excluded.date_updated,
            },
        )
        await self._session.execute(upsert_stmt)
        await self._session.flush()

        if delete_stale_records:
            # First, find affiliation IDs and internal_ids to be deleted
            subquery = select(
                SqlAffiliation.id, SqlAffiliation.internal_id
            ).where(SqlAffiliation.date_updated < now)
            result = await self._session.execute(subquery)
            to_delete = result.all()
            affiliation_ids = [row.id for row in to_delete]
            affiliation_internal_ids = [row.internal_id for row in to_delete]
            if affiliation_internal_ids:
                self._logger.info(
                    "Deleting stale affiliations not in authordb.yaml",
                    internal_ids=affiliation_internal_ids,
                )

            # Delete related author-affiliation rows
            if affiliation_ids:
                delete_author_affiliations_stmt = delete(
                    SqlAuthorAffiliation
                ).where(
                    SqlAuthorAffiliation.affiliation_id.in_(affiliation_ids)
                )
                await self._session.execute(delete_author_affiliations_stmt)

            # Now delete the affiliations
            if affiliation_ids:
                delete_stmt = delete(SqlAffiliation).where(
                    SqlAffiliation.id.in_(affiliation_ids)
                )
                await self._session.execute(delete_stmt)
                await self._session.flush()

    async def upsert_authors(
        self, authors: Sequence[Author], *, delete_stale_records: bool = False
    ) -> None:
        """Upsert authors into the database.

        Parameters
        ----------
        authors
            A sequence of Author domain models to be upserted into the
            database.
        delete_stale_records
            If True, delete authors that are not in the provided list.
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

        # Optionally delete stale records based on having `date_updated < now`
        if delete_stale_records:
            # First, find author IDs and internal_ids to be deleted
            subquery = select(SqlAuthor.id, SqlAuthor.internal_id).where(
                SqlAuthor.date_updated < now
            )
            result = await self._session.execute(subquery)
            to_delete = result.all()
            author_ids = [row.id for row in to_delete]
            author_internal_ids = [row.internal_id for row in to_delete]
            if author_internal_ids:
                self._logger.info(
                    "Deleting stale authors not in authordb.yaml",
                    internal_ids=author_internal_ids,
                )

            # Delete related author-affiliation rows
            if author_ids:
                delete_affiliations_stmt = delete(SqlAuthorAffiliation).where(
                    SqlAuthorAffiliation.author_id.in_(author_ids)
                )
                await self._session.execute(delete_affiliations_stmt)

            # Now delete the authors
            if author_ids:
                delete_stmt = delete(SqlAuthor).where(
                    SqlAuthor.id.in_(author_ids)
                )
                await self._session.execute(delete_stmt)
                await self._session.flush()

    async def upsert_collaborations(
        self,
        collaborations: Sequence[Collaboration],
        *,
        delete_stale_records: bool = False,
    ) -> None:
        """Upsert collaborations into the database.

        Parameters
        ----------
        collaborations
            A sequence of Collaboration domain models to be upserted into the
            database.
        delete_stale_records
            If True, delete collaborations that are not in the provided list.
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

        if delete_stale_records:
            delete_stmt = delete(SqlCollaboration).where(
                SqlCollaboration.date_updated < now,
            )
            await self._session.execute(delete_stmt)
            await self._session.flush()


@dataclass(slots=True)
class AuthorsCursor(PaginationCursor[Author]):
    """Cursor for paginating authors, sorted by their internal ID."""

    internal_id: str
    """The internal ID of the author."""

    @override
    @classmethod
    def from_entry(cls, entry: Author, *, reverse: bool = False) -> Self:
        """Create a cursor from an author entry as the bound.

        Parameters
        ----------
        entry
            The author entry.
        reverse
            Whether the cursor is for the previous page.

        Returns
        -------
        AuthorsCursor
            The cursor object.
        """
        return cls(internal_id=entry.internal_id, previous=reverse)

    @override
    @classmethod
    def from_str(cls, cursor: str) -> Self:
        """Create a cursor from a string.

        Parameters
        ----------
        cursor
            The cursor string.

        Returns
        -------
        AuthorsCursor
            The cursor object.
        """
        previous_prefix = "p__"
        if cursor.startswith(previous_prefix):
            internal_id = cursor.removeprefix(previous_prefix)
            previous = True
        else:
            internal_id = cursor
            previous = False
        return cls(internal_id=internal_id, previous=previous)

    @override
    @classmethod
    def apply_order(cls, stmt: Select, *, reverse: bool = False) -> Select:
        """Apply the sort order of the cursor to a select statement.

        Parameters
        ----------
        stmt
            The SQLAlchemy statement to apply ordering to.
        reverse
            Whether the ordering should be reversed.

        Returns
        -------
        Select
            The modified SQLAlchemy statement with ordering applied.
        """
        return stmt.order_by(
            SqlAuthor.internal_id.desc()
            if reverse
            else SqlAuthor.internal_id.asc()
        )

    @override
    def apply_cursor(self, stmt: Select) -> Select:
        """Apply the cursor to a select statement.

        Parameters
        ----------
        stmt
            The SQLAlchemy statement to apply the cursor to.

        Returns
        -------
        Select
            The modified SQLAlchemy statement with the cursor applied.
        """
        if self.previous:
            return stmt.where(SqlAuthor.internal_id < self.internal_id)

        # In forward direction, include the current ID of the cursor
        return stmt.where(SqlAuthor.internal_id >= self.internal_id)

    @override
    def invert(self) -> Self:
        """Invert the cursor.

        Returns
        -------
        AuthorsCursor
            The inverted cursor.
        """
        return type(self)(
            internal_id=self.internal_id, previous=not self.previous
        )

    def __str__(self) -> str:
        """Convert the cursor to a string.

        Returns
        -------
        str
            The string representation of the cursor.
        """
        return f"p__{self.internal_id}" if self.previous else self.internal_id
