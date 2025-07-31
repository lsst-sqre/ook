"""Reusable query utilities for loading authors with affiliations."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, case, func, select
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by

from ook.dbschema.authors import (
    SqlAffiliation,
    SqlAuthor,
    SqlAuthorAffiliation,
)

__all__ = [
    "create_affiliations_json_object",
    "create_all_authors_stmt",
    "create_author_affiliations_subquery",
    "create_author_by_internal_id_stmt",
    "create_author_json_object",
    "create_author_search_stmt",
    "create_author_with_affiliations_columns",
]


def create_author_json_object() -> Any:
    """Create the JSON object expression for an Author domain model, including
    affiliations as a JSON array.

    Returns
    -------
    Any
        SQLAlchemy JSON object expression for author data.
    """
    return func.json_build_object(
        "internal_id",
        SqlAuthor.internal_id,
        "surname",
        SqlAuthor.surname,
        "given_name",
        SqlAuthor.given_name,
        "orcid",
        SqlAuthor.orcid,
        "email",
        SqlAuthor.email,
        "notes",
        SqlAuthor.notes,
        "affiliations",
        create_author_affiliations_subquery(),
    )


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


def create_author_affiliations_subquery() -> Any:
    """Create a subquery for author affiliations as JSON array.

    Returns
    -------
    Any
        SQLAlchemy subquery expression for affiliations.
    """
    affiliations_subquery = (
        select(
            func.json_agg(
                aggregate_order_by(
                    create_affiliations_json_object().cast(JSONB),
                    SqlAuthorAffiliation.position,
                )
            ).label("affiliations")
        )
        .select_from(SqlAffiliation)
        .join(
            SqlAuthorAffiliation,
            SqlAffiliation.id == SqlAuthorAffiliation.affiliation_id,
        )
        .where(SqlAuthorAffiliation.author_id == SqlAuthor.id)
    ).scalar_subquery()

    return case(
        (affiliations_subquery.is_(None), func.json_build_array()),
        else_=affiliations_subquery,
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

    return [
        author_table.internal_id,
        author_table.surname,
        author_table.given_name,
        author_table.orcid,
        author_table.email,
        author_table.notes,
        create_author_affiliations_subquery().label("affiliations"),
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
    """Create a select statement to get all authors with affiliations.

    Returns
    -------
    Select
        SQLAlchemy select statement ready for execution, suitable for
        pagination.
    """
    columns = create_author_with_affiliations_columns()
    return select(*columns)


def create_author_search_stmt(search_query: str) -> Select:
    """Create a select statement for author search using basic text matching.

    This implements simple substring matching for author names,
    supporting various name formats and returning results with
    basic relevance scores.

    Parameters
    ----------
    search_query
        The search query string to match against author names.

    Returns
    -------
    Select
        SQLAlchemy select statement ready for execution, suitable for
        pagination. Results include a 'score' column with relevance.
    """
    # Handle multi-word queries (e.g., "Jonathan Sick")
    query_parts = search_query.strip().split()

    if len(query_parts) == 2:
        # Likely first name and last name
        first_part, second_part = query_parts
        # Simple relevance scoring for multi-word queries
        score_expr = case(
            # Exact match: given_name matches first, surname matches second
            (
                (SqlAuthor.given_name.ilike(first_part))
                & (SqlAuthor.surname.ilike(second_part)),
                100,
            ),
            # Exact match: surname matches first, given_name matches second
            (
                (SqlAuthor.surname.ilike(first_part))
                & (SqlAuthor.given_name.ilike(second_part)),
                95,
            ),
            # Partial match: given_name starts with first, surname with second
            (
                (SqlAuthor.given_name.ilike(first_part + "%"))
                & (SqlAuthor.surname.ilike(second_part + "%")),
                90,
            ),
            # Partial match: surname starts with first, given_name with second
            (
                (SqlAuthor.surname.ilike(first_part + "%"))
                & (SqlAuthor.given_name.ilike(second_part + "%")),
                85,
            ),
            # Contains match: given_name contains first, surname second
            (
                (SqlAuthor.given_name.ilike("%" + first_part + "%"))
                & (SqlAuthor.surname.ilike("%" + second_part + "%")),
                80,
            ),
            # Contains match: surname contains first, given_name second
            (
                (SqlAuthor.surname.ilike("%" + first_part + "%"))
                & (SqlAuthor.given_name.ilike("%" + second_part + "%")),
                75,
            ),
            # Single field matches (fallback for partial matches)
            (SqlAuthor.surname.ilike("%" + search_query + "%"), 60),
            (SqlAuthor.given_name.ilike("%" + search_query + "%"), 50),
            # Default score
            else_=40,
        ).label("score")

        # Search condition for multi-word queries
        search_condition = (
            # Match across both fields
            (
                (SqlAuthor.given_name.ilike("%" + first_part + "%"))
                & (SqlAuthor.surname.ilike("%" + second_part + "%"))
            )
            | (
                (SqlAuthor.surname.ilike("%" + first_part + "%"))
                & (SqlAuthor.given_name.ilike("%" + second_part + "%"))
            )
            |
            # Fallback to single field matching
            SqlAuthor.surname.ilike("%" + search_query + "%")
            | SqlAuthor.given_name.ilike("%" + search_query + "%")
        )
    else:
        # Single word or complex query - use original logic
        score_expr = case(
            # Exact match on surname gets highest score
            (SqlAuthor.surname.ilike(search_query), 100),
            # Exact match on given name gets high score
            (SqlAuthor.given_name.ilike(search_query), 90),
            # Surname starts with search term
            (SqlAuthor.surname.ilike(search_query + "%"), 80),
            # Given name starts with search term
            (SqlAuthor.given_name.ilike(search_query + "%"), 70),
            # Surname contains search term
            (SqlAuthor.surname.ilike("%" + search_query + "%"), 60),
            # Given name contains search term
            (SqlAuthor.given_name.ilike("%" + search_query + "%"), 50),
            # Default score for other matches
            else_=40,
        ).label("score")

        # Simple substring matching
        search_condition = SqlAuthor.surname.ilike(
            "%" + search_query + "%"
        ) | SqlAuthor.given_name.ilike("%" + search_query + "%")

    # Main search query with text matching
    return (
        select(
            SqlAuthor.internal_id,
            SqlAuthor.surname,
            SqlAuthor.given_name,
            SqlAuthor.orcid,
            SqlAuthor.email,
            SqlAuthor.notes,
            create_author_affiliations_subquery().label("affiliations"),
            score_expr,
        )
        .where(search_condition)
        .order_by(score_expr.desc(), SqlAuthor.internal_id.asc())
    )
