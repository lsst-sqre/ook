"""Search strategy base classes and implementations for author search."""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy import and_, case, func, or_
from sqlalchemy.sql.elements import ColumnElement

from ook.dbschema.authors import SqlAuthor
from ook.domain.authors import ParsedName

__all__ = [
    "ComponentStrategy",
    "InitialStrategy",
    "SearchStrategy",
    "TrigramStrategy",
]


class SearchStrategy(ABC):
    """Abstract base class for author search strategies.

    Each search strategy implements a different approach to matching
    authors based on parsed name components. Strategies can be combined
    to provide comprehensive search capabilities.
    """

    @abstractmethod
    def build_condition(self, parsed_name: ParsedName) -> ColumnElement[bool]:
        """Build the WHERE condition for this search strategy.

        Parameters
        ----------
        parsed_name
            The parsed name components to search for.

        Returns
        -------
        ColumnElement[bool]
            SQLAlchemy condition expression for filtering authors.
        """

    @abstractmethod
    def build_score_expr(self, parsed_name: ParsedName) -> ColumnElement[int]:
        """Build the relevance score expression for this strategy.

        Parameters
        ----------
        parsed_name
            The parsed name components to score against.

        Returns
        -------
        ColumnElement[int]
            SQLAlchemy expression that evaluates to a relevance score (0-100).
        """


class TrigramStrategy(SearchStrategy):
    """Uses PostgreSQL trigram similarity on the search_vector column.

    This strategy leverages PostgreSQL's pg_trgm extension to provide
    fuzzy matching with typo tolerance. It uses the pre-computed
    search_vector column which contains name variations.
    """

    def __init__(self, similarity_threshold: float = 0.1) -> None:
        """Initialize the trigram strategy.

        Parameters
        ----------
        similarity_threshold
            Minimum similarity score (0.0-1.0) to consider a match.
            Lower values are more permissive.
        """
        self.similarity_threshold = similarity_threshold

    def build_condition(self, parsed_name: ParsedName) -> ColumnElement[bool]:
        """Build trigram similarity condition using search_vector.

        Uses PostgreSQL's similarity() function with a configurable threshold.
        """
        return (
            func.similarity(SqlAuthor.search_vector, parsed_name.original)
            > self.similarity_threshold
        )

    def build_score_expr(self, parsed_name: ParsedName) -> ColumnElement[int]:
        """Build trigram-based relevance scoring.

        Combines multiple PostgreSQL trigram functions:
        - similarity(): Full trigram similarity
        - word_similarity(): Partial word matching
        - Exact substring match bonus
        """
        return func.greatest(
            # Full trigram similarity (scaled to 0-100)
            func.similarity(SqlAuthor.search_vector, parsed_name.original)
            * 100,
            # Word similarity for partial matches (scaled to 0-90)
            func.word_similarity(parsed_name.original, SqlAuthor.search_vector)
            * 90,
            # Exact substring match bonus
            case(
                (
                    SqlAuthor.search_vector.ilike(f"%{parsed_name.original}%"),
                    85,
                ),
                else_=0,
            ),
        )


class ComponentStrategy(SearchStrategy):
    """Matches individual name components with middle initial flexibility.

    This strategy provides precise matching on surname and given name
    components, with special handling for middle initial variations.
    """

    def build_condition(self, parsed_name: ParsedName) -> ColumnElement[bool]:
        """Build condition matching individual name components.

        Supports flexible matching where middle initials can be
        included or omitted in the search query.
        """
        conditions: list[ColumnElement[bool]] = []

        if parsed_name.surname:
            conditions.append(
                SqlAuthor.surname.ilike(f"%{parsed_name.surname}%")
            )

        if parsed_name.given_name:
            # Extract first name without middle initials for flexible matching
            first_name = parsed_name.given_name.split()[0]

            # Match full given name OR just first name (for middle initial
            # flexibility)
            given_conditions = [
                SqlAuthor.given_name.ilike(f"%{parsed_name.given_name}%"),
                SqlAuthor.given_name.ilike(f"{first_name}%"),
            ]
            conditions.append(or_(*given_conditions))

        # Return combined condition or always-true if no conditions
        return or_(*conditions) if conditions else SqlAuthor.id.isnot(None)

    def build_score_expr(self, parsed_name: ParsedName) -> ColumnElement[int]:
        """Build tiered scoring based on match quality.

        Implements a hierarchy:
        1. Exact match with full given name (100)
        2. Exact surname + first name match (95)
        3. Prefix matches (85)
        4. Partial matches with flexibility (75)
        """
        score_cases = []

        if parsed_name.surname and parsed_name.given_name:
            first_name = parsed_name.given_name.split()[0]

            # Exact match with full given name (including middle initials)
            score_cases.append(
                (
                    and_(
                        SqlAuthor.surname.ilike(parsed_name.surname),
                        SqlAuthor.given_name.ilike(parsed_name.given_name),
                    ),
                    100,
                )
            )

            # Exact surname + first name matches (ignoring middle initials)
            score_cases.append(
                (
                    and_(
                        SqlAuthor.surname.ilike(parsed_name.surname),
                        SqlAuthor.given_name.ilike(f"{first_name}%"),
                    ),
                    95,
                )
            )

            # Prefix matches
            score_cases.append(
                (
                    and_(
                        SqlAuthor.surname.ilike(f"{parsed_name.surname}%"),
                        SqlAuthor.given_name.ilike(
                            f"{parsed_name.given_name}%"
                        ),
                    ),
                    85,
                )
            )

            # Partial matches with flexibility
            score_cases.append(
                (
                    and_(
                        SqlAuthor.surname.ilike(f"%{parsed_name.surname}%"),
                        SqlAuthor.given_name.ilike(f"{first_name}%"),
                    ),
                    75,
                )
            )

        # Handle surname-only queries
        if parsed_name.surname:
            score_cases.append(
                (SqlAuthor.surname.ilike(parsed_name.surname), 70)
            )
            score_cases.append(
                (SqlAuthor.surname.ilike(f"{parsed_name.surname}%"), 65)
            )
            score_cases.append(
                (SqlAuthor.surname.ilike(f"%{parsed_name.surname}%"), 60)
            )

        # Handle given name-only queries
        if parsed_name.given_name:
            score_cases.append(
                (SqlAuthor.given_name.ilike(parsed_name.given_name), 60)
            )
            score_cases.append(
                (SqlAuthor.given_name.ilike(f"{parsed_name.given_name}%"), 55)
            )
            score_cases.append(
                (SqlAuthor.given_name.ilike(f"%{parsed_name.given_name}%"), 50)
            )

        return case(*score_cases, else_=40)


class InitialStrategy(SearchStrategy):
    """Handles initial-based matching for abbreviated name searches.

    This strategy specializes in matching queries with initials
    (e.g., "Sick, J") against full given names in the database.
    """

    def build_condition(self, parsed_name: ParsedName) -> ColumnElement[bool]:
        """Build condition for initial-based matching.

        Matches each initial against the start of words in the given_name,
        combined with surname matching if present.
        """
        if not parsed_name.initials:
            # Return always-false condition if no initials
            return SqlAuthor.id.is_(None)

        # Match each initial against given name words
        conditions: list[ColumnElement[bool]] = [
            SqlAuthor.given_name.ilike(f"{initial}%")
            for initial in parsed_name.initials
        ]

        # Add surname condition if present
        if parsed_name.surname:
            conditions.append(
                SqlAuthor.surname.ilike(f"%{parsed_name.surname}%")
            )

        # All conditions must match for initials strategy
        return and_(*conditions) if len(conditions) > 1 else conditions[0]

    def build_score_expr(self, parsed_name: ParsedName) -> ColumnElement[int]:
        """Build scoring for initial matches.

        Provides high scores for initial matches, with bonuses for
        exact surname matches and multiple initial matches.
        """
        if not parsed_name.initials:
            # Return a literal zero for no initial matches
            return case((SqlAuthor.id.isnot(None), 0), else_=0)

        score_cases = []

        # Base score for initial matches
        base_score = 80

        # Bonus for exact surname match
        if parsed_name.surname:
            score_cases.append(
                (
                    SqlAuthor.surname.ilike(parsed_name.surname),
                    base_score + 15,  # 95 total
                )
            )
            score_cases.append(
                (
                    SqlAuthor.surname.ilike(f"{parsed_name.surname}%"),
                    base_score + 10,  # 90 total
                )
            )

        # Default initial match score
        score_cases.append((SqlAuthor.id.isnot(None), base_score))

        return case(*score_cases, else_=0)
