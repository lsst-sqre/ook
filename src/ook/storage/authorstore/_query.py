"""Reusable query utilities for loading authors with affiliations."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, case, cast, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by
from sqlalchemy.sql.sqltypes import Text

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


def create_author_search_stmt(search_query: str) -> Select:
    """Create a select statement for fuzzy author search.

    This implements trigram-based fuzzy matching for author names,
    supporting various name formats and returning results with
    relevance scores.

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
    # Handle "Lastname, Firstname" format by creating normalized query
    search_terms_cte = select(
        cast(search_query, Text).label("original_query"),
        case(
            (
                cast(search_query, Text).like("%,%"),
                func.trim(func.split_part(search_query, ",", 2))
                + " "
                + func.trim(func.split_part(search_query, ",", 1)),
            ),
            else_=cast(search_query, Text),
        ).label("normalized_query"),
    ).cte("search_terms")

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

    # Calculate composite relevance score
    score_expr = func.greatest(
        # Direct similarity to search vector
        func.similarity(
            SqlAuthor.search_vector, search_terms_cte.c.original_query
        )
        * 100,
        func.similarity(
            SqlAuthor.search_vector, search_terms_cte.c.normalized_query
        )
        * 100,
        # Surname similarity (weighted higher)
        func.similarity(SqlAuthor.surname, search_terms_cte.c.original_query)
        * 120,
        func.similarity(SqlAuthor.surname, search_terms_cte.c.normalized_query)
        * 120,
        # Given name similarity
        func.coalesce(
            func.similarity(
                SqlAuthor.given_name, search_terms_cte.c.original_query
            )
            * 80,
            0,
        ),
        func.coalesce(
            func.similarity(
                SqlAuthor.given_name, search_terms_cte.c.normalized_query
            )
            * 80,
            0,
        ),
        # Exact substring match bonuses
        case(
            (SqlAuthor.surname.ilike("%" + search_query + "%"), 90),
            (SqlAuthor.given_name.ilike("%" + search_query + "%"), 70),
            else_=0,
        ),
    ).label("score")

    # Main search query with trigram filtering
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
            score_expr,
        )
        .select_from(SqlAuthor)
        .join(search_terms_cte, text("true"))  # Cross join with search terms
        .join(
            affiliations_subquery,
            SqlAuthor.id == affiliations_subquery.c.author_id,
            isouter=True,
        )
        .where(
            # Use trigram index for initial filtering
            (
                SqlAuthor.search_vector.op("%")(
                    search_terms_cte.c.original_query
                )
            )
            | (
                SqlAuthor.search_vector.op("%")(
                    search_terms_cte.c.normalized_query
                )
            )
            | (SqlAuthor.surname.op("%")(search_terms_cte.c.original_query))
            | (SqlAuthor.surname.op("%")(search_terms_cte.c.normalized_query))
            | (SqlAuthor.given_name.op("%")(search_terms_cte.c.original_query))
            | (
                SqlAuthor.given_name.op("%")(
                    search_terms_cte.c.normalized_query
                )
            )
            # Include exact substring matches
            | SqlAuthor.surname.ilike("%" + search_query + "%")
            | SqlAuthor.given_name.ilike("%" + search_query + "%")
        )
        .having(score_expr > 20)  # Minimum relevance threshold
        .order_by(score_expr.desc(), SqlAuthor.internal_id.asc())
    )
