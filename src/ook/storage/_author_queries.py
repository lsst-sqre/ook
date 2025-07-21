"""Reusable query utilities for loading authors with affiliations."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, case, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_scoped_session

from ook.dbschema.authors import (
    SqlAffiliation,
    SqlAuthor,
    SqlAuthorAffiliation,
)
from ook.dbschema.resources import SqlContributor
from ook.domain.authors import Author
from ook.domain.resources import Contributor, ContributorRole

__all__ = [
    "create_affiliations_json_object",
    "create_all_authors_stmt",
    "create_author_by_internal_id_stmt",
    "create_author_with_affiliations_columns",
    "load_contributors_for_resources",
]


def create_affiliations_json_object() -> Any:
    """Create the JSON object expression for affiliation data.

    This creates the standardized JSON structure for affiliation data
    that's used consistently across different query builders.

    Returns
    -------
    Any
        SQLAlchemy JSON object expression for affiliation data.
    """
    return func.json_build_object(
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
    )


def create_author_with_affiliations_columns(
    author_table: type[SqlAuthor] | None = None,
) -> list:
    """Create column expressions for selecting author data with affiliations.

    This creates the same column structure that the Author domain model
    expects, with affiliations as a JSON array to avoid N+1 queries.

    Parameters
    ----------
    author_table
        The SqlAuthor table or alias to use. If None, uses SqlAuthor directly.

    Returns
    -------
    list
        List of column expressions for use in a select() statement.
    """
    if author_table is None:
        author_table = SqlAuthor

    # Subquery to get affiliations as a JSON array for this author
    # Use PostgreSQL's json_agg with ORDER BY for proper ordering
    affiliations_subquery = (
        select(
            func.json_agg(create_affiliations_json_object().cast(JSONB)).label(
                "affiliations"
            )
        )
        .select_from(SqlAffiliation)
        .join(
            SqlAuthorAffiliation,
            SqlAffiliation.id == SqlAuthorAffiliation.affiliation_id,
        )
        .where(SqlAuthorAffiliation.author_id == author_table.id)
    ).scalar_subquery()

    return [
        author_table.internal_id,
        author_table.surname,
        author_table.given_name,
        author_table.orcid,
        author_table.email,
        author_table.notes,
        case(
            (affiliations_subquery.is_(None), func.json_build_array()),
            else_=affiliations_subquery,
        ).label("affiliations"),
    ]


def create_author_by_internal_id_stmt(internal_id: str) -> Select:
    """Create a complete statement to get an author by internal_id.

    Parameters
    ----------
    internal_id
        The internal ID of the author to retrieve.

    Returns
    -------
    Select
        SQLAlchemy select statement ready for execution.
    """
    columns = create_author_with_affiliations_columns()
    return select(*columns).where(SqlAuthor.internal_id == internal_id)


def create_all_authors_stmt() -> Select:
    """Create a complete statement to get all authors with affiliations.

    Returns
    -------
    Select
        SQLAlchemy select statement ready for execution, suitable for
        pagination.
    """
    # Subquery to get affiliations as a JSON array for each author
    affiliations_subquery = (
        select(
            SqlAuthor.id.label("author_id"),
            func.json_agg(create_affiliations_json_object().cast(JSONB)).label(
                "affiliations"
            ),
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
        .order_by(SqlAuthor.id, func.array_agg(SqlAuthorAffiliation.position))
    ).alias("affiliations_subquery")

    # Main query to get all authors with their affiliations
    return (
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


async def load_contributors_for_resources(
    session: async_scoped_session, resource_ids: list[int]
) -> dict[int, list]:
    """Load contributors for multiple resources using JSON aggregation.

    This avoids the N+1 query problem and async lazy loading issues by
    fetching all contributor data in a single query with JSON aggregation.

    Parameters
    ----------
    session
        The SQLAlchemy async session.
    resource_ids
        List of resource IDs to load contributors for.

    Returns
    -------
    dict[int, list]
        Dictionary mapping resource_id to list of Contributor domain objects.
    """
    if not resource_ids:
        return {}

    # Query to get all contributors with their authors and affiliations
    # using JSON aggregation to avoid lazy loading
    stmt = (
        select(
            SqlContributor.resource_id,
            SqlContributor.order,
            SqlContributor.role,
            *create_author_with_affiliations_columns(SqlAuthor),
        )
        .select_from(SqlContributor)
        .join(SqlAuthor, SqlContributor.author_id == SqlAuthor.id)
        .where(SqlContributor.resource_id.in_(resource_ids))
        .order_by(SqlContributor.resource_id, SqlContributor.order)
    )

    result = await session.execute(stmt)
    rows = result.all()

    # Group contributors by resource_id
    contributors_by_resource: dict[int, list[Contributor]] = {}
    for row in rows:
        resource_id = row.resource_id
        if resource_id not in contributors_by_resource:
            contributors_by_resource[resource_id] = []

        # Create Author domain object from the row data
        author = Author.model_validate(row, from_attributes=True)

        # Create Contributor domain object
        # Find the enum member by its value since we store the enum value in DB
        role = None
        for member in ContributorRole:
            if member.value == row.role:
                role = member
                break
        if role is None:
            # Fallback - try direct construction
            role = ContributorRole(row.role)

        contributor = Contributor(
            resource_id=resource_id,
            author=author,
            role=role,
            order=row.order,
        )
        contributors_by_resource[resource_id].append(contributor)

    return contributors_by_resource
