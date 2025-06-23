"""Storage interface to the terms table in the database."""

from __future__ import annotations

from dataclasses import dataclass

from safir.datetime import current_datetime
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import async_scoped_session
from sqlalchemy.sql.expression import case
from structlog.stdlib import BoundLogger

from ook.dbschema.glossary import SqlTerm, term_relationships
from ook.domain.glossary import GlossaryTerm
from ook.storage.lssttexmf import GlossaryDef, GlossaryDefEs

__all__ = ["GlossaryStore"]


class GlossaryStore:
    """Storage interface to the terms table in a database."""

    def __init__(
        self, session: async_scoped_session, logger: BoundLogger
    ) -> None:
        self._session = session
        self._logger = logger

    async def clear_glossary(self) -> None:
        """Delete all existing glossary terms from the database.

        Thanks to ON DELETE CASCADE in the term_relationships foreign keys,
        we only need to delete the terms and the relationships will be
        automatically deleted.
        """
        delete_terms_stmt = delete(SqlTerm)
        await self._session.execute(delete_terms_stmt)
        await self._session.flush()

    async def search_terms(
        self,
        *,
        search_term: str,
        contexts: list[str] | None = None,
        include_abbr: bool = True,
        include_terms: bool = True,
        search_definitions: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[GlossaryTerm]:
        """Search for glossary a term in the database."""
        # Create an explicit empty JSON array literal
        empty_json_array = func.json_build_array()

        # Subquery to get related terms as a JSON array
        related_terms_subquery = (
            select(
                term_relationships.c.source_term_id.label("term_id"),
                func.coalesce(
                    func.json_agg(
                        func.json_build_object(
                            "term",
                            SqlTerm.term,
                            "definition",
                            SqlTerm.definition,
                            "definition_es",
                            SqlTerm.definition_es,
                            "is_abbr",
                            SqlTerm.is_abbr,
                            "contexts",
                            SqlTerm.contexts,
                            "related_documentation",
                            SqlTerm.related_documentation,
                        )
                    ),
                    empty_json_array,
                ).label("related_terms"),
            )
            .select_from(term_relationships)
            .join(SqlTerm, term_relationships.c.related_term_id == SqlTerm.id)
            .group_by(term_relationships.c.source_term_id)
        ).alias("related_terms_subquery")

        # Subquery to get referenced_by terms as a JSON array
        referenced_by_subquery = (
            select(
                term_relationships.c.related_term_id.label("term_id"),
                func.coalesce(
                    func.json_agg(
                        func.json_build_object(
                            "term",
                            SqlTerm.term,
                            "definition",
                            SqlTerm.definition,
                            "definition_es",
                            SqlTerm.definition_es,
                            "is_abbr",
                            SqlTerm.is_abbr,
                            "contexts",
                            SqlTerm.contexts,
                            "related_documentation",
                            SqlTerm.related_documentation,
                        )
                    ),
                    empty_json_array,
                ).label("referenced_by"),
            )
            .select_from(term_relationships)
            .join(SqlTerm, term_relationships.c.source_term_id == SqlTerm.id)
            .group_by(term_relationships.c.related_term_id)
        ).alias("referenced_by_subquery")

        # Shape results to match the GlossaryTerm model
        main_query = select(
            SqlTerm.id,
            SqlTerm.term,
            SqlTerm.definition,
            SqlTerm.definition_es,
            SqlTerm.contexts,
            SqlTerm.is_abbr,
            SqlTerm.related_documentation,
            case(
                (
                    related_terms_subquery.c.related_terms.is_(None),
                    empty_json_array,
                ),
                else_=related_terms_subquery.c.related_terms,
            ).label("related_terms"),
            case(
                (
                    referenced_by_subquery.c.referenced_by.is_(None),
                    empty_json_array,
                ),
                else_=referenced_by_subquery.c.referenced_by,
            ).label("referenced_by"),
        )

        # Convert search term to lowercase for case-insensitive matching
        search_term_lower = search_term.lower()

        # Create a combined relevance expression for ordering results
        # Exact match (highest priority)
        exact_match = case(
            *[(func.lower(SqlTerm.term) == search_term_lower, 100)], else_=0
        )
        # Term begins with search term
        begins_with = case(
            *[(func.lower(SqlTerm.term).like(f"{search_term_lower}%"), 50)],
            else_=0,
        )
        # Term contains search term
        contains_term = case(
            *[(func.lower(SqlTerm.term).like(f"%{search_term_lower}%"), 25)],
            else_=0,
        )
        # Trigram similarity components
        term_similarity = func.similarity(SqlTerm.term, search_term) * 20
        # Consider the definition only if requested
        if search_definitions:
            # Definition contains search term
            definition_contains = case(
                *[
                    (
                        func.lower(SqlTerm.definition).like(
                            f"%{search_term_lower}%"
                        ),
                        10,
                    )
                ],
                else_=0,
            )
            # Trigram similarity components
            def_similarity = (
                func.similarity(SqlTerm.definition, search_term) * 5
            )
            # Combine all factors into a relevance score
            relevance_expr = (
                exact_match
                + begins_with
                + contains_term
                + definition_contains
                + term_similarity
                + def_similarity
            ).label("relevance")
        else:
            # Combine all factors into a relevance score (no definition
            # considered)
            relevance_expr = (
                exact_match + begins_with + contains_term + term_similarity
            ).label("relevance")

        # Filter to only include relevant terms
        if search_definitions:
            main_query = main_query.filter(
                or_(
                    func.lower(SqlTerm.term).like(f"%{search_term_lower}%"),
                    func.similarity(SqlTerm.term, search_term) > 0.3,
                    func.lower(SqlTerm.definition).like(
                        f"%{search_term_lower}%"
                    ),
                    func.similarity(SqlTerm.definition, search_term) > 0.1,
                )
            )
        else:
            main_query = main_query.filter(
                or_(
                    func.lower(SqlTerm.term).like(f"%{search_term_lower}%"),
                    func.similarity(SqlTerm.term, search_term) > 0.3,
                )
            )

        # Apply context filters if provided
        if contexts and len(contexts) > 0:
            # Return terms where at least one context matches any of the
            # provided contexts
            main_query = main_query.filter(SqlTerm.contexts.overlap(contexts))

        if not include_abbr and include_terms:
            # Exclude abbreviations
            main_query = main_query.filter(SqlTerm.is_abbr.is_(False))
        elif include_abbr and not include_terms:
            # Exclude terms
            main_query = main_query.filter(SqlTerm.is_abbr.is_(True))

        # Join with the subqueries for related terms and referenced_by terms
        main_query = (
            main_query.select_from(SqlTerm)
            .outerjoin(  # Use outerjoin instead of join with isouter=True
                related_terms_subquery,
                SqlTerm.id == related_terms_subquery.c.term_id,
            )
            .outerjoin(
                referenced_by_subquery,
                SqlTerm.id == referenced_by_subquery.c.term_id,
            )
        )

        # Order by relevance score (descending)
        main_query = (
            main_query.order_by(text("relevance DESC"), SqlTerm.term)
            .add_columns(relevance_expr.label("relevance"))
            .params(
                search_term=search_term, search_term_lower=search_term_lower
            )
        )

        # Apply pagination
        main_query = main_query.limit(limit).offset(offset)

        # Execute the query
        results = await self._session.execute(main_query)
        result_rows = results.all()

        # Debug: Log the first result if available
        if result_rows:
            self._logger.debug(
                "First search result",
                term=result_rows[0].term,
                related_terms_type=type(result_rows[0].related_terms),
                related_terms=result_rows[0].related_terms,
                referenced_by_type=type(result_rows[0].referenced_by),
                referenced_by=result_rows[0].referenced_by,
            )

        # Convert results to GlossaryTerm objects
        glossary_terms = [
            GlossaryTerm.model_validate(row, from_attributes=True)
            for row in result_rows
        ]

        # Debug: Log the first processed term if available
        if glossary_terms:
            self._logger.debug(
                "First glossary term after validation",
                term=glossary_terms[0].term,
                related_terms=glossary_terms[0].related_terms,
                referenced_by=glossary_terms[0].referenced_by,
            )

        return glossary_terms

    async def store_glossarydefs(
        self, *, terms: list[GlossaryDef], es_translations: list[GlossaryDefEs]
    ) -> None:
        """Store glossary terms from the GlossaryDef data loaded from
        lsst-texmf's glossarydefs.csv and glossarydefs_es.csv files.

        This method completely replaces all existing terms with the new terms.
        """
        now = current_datetime(microseconds=False)

        # Delete all existing terms
        await self.clear_glossary()

        # De-duplicate terms by term and contexts. Sometimes people
        # accidentally duplicate terms.
        original_length = len(terms)
        seen = {}
        duplicates = []
        for term in terms:
            key = TermMapKey(
                term=term.term, contexts=tuple(sorted(term.contexts))
            )
            if key in seen:
                duplicates.append(term)
            else:
                seen[key] = term
        terms = list(seen.values())
        if duplicates:
            self._logger.warning(
                "Glossary terms were de-duplicated",
                original_length=original_length,
                new_length=len(terms),
                duplicates=[
                    {
                        "term": t.term,
                        "contexts": sorted(t.contexts),
                        "definition": t.definition,
                    }
                    for t in duplicates
                ],
            )

        # Map of term rows, keyed by (term, contexts)
        term_map: dict[TermMapKey, TermMapValue] = {}

        # Create all terms
        for term in terms:
            sorted_contexts = sorted(term.contexts)

            sql_term = SqlTerm(
                term=term.term,
                definition=term.definition,
                is_abbr=term.type == "A",
                contexts=sorted_contexts,
                related_documentation=term.documentation_tags,
                date_updated=now,
            )
            self._session.add(sql_term)

            key = TermMapKey(
                term=term.term,
                contexts=tuple(sorted_contexts),
            )
            term_map[key] = TermMapValue(
                sql_term=sql_term,
                domain_term=term,
            )

        await self._associate_related_terms(term_map)

        await self._add_es_translations(term_map, es_translations)

        await self._session.flush()

    async def _associate_related_terms(
        self, term_map: dict[TermMapKey, TermMapValue]
    ) -> None:
        """Associate related terms in the database."""
        for term_map_value in term_map.values():
            domain_term = term_map_value.domain_term
            sql_term = term_map_value.sql_term

            if not domain_term.related_terms:
                continue

            for related_term_name in domain_term.related_terms:
                # Get all rows with the same term name
                related_terms = [
                    v.sql_term
                    for k, v in term_map.items()
                    if k.term == related_term_name
                ]

                if not related_terms:
                    self._logger.warning(
                        "Related glossary term not found in database",
                        source_term=domain_term.term,
                        related_term=related_term_name,
                    )
                    continue

                target_term = None
                if len(related_terms) == 1:
                    target_term = related_terms[0]
                else:
                    # Try to find a related term with matching contexts
                    matching_contexts_term = None
                    for rt in related_terms:
                        if any(
                            ctx in rt.contexts for ctx in domain_term.contexts
                        ):
                            matching_contexts_term = rt
                            break

                    if matching_contexts_term:
                        target_term = matching_contexts_term
                    else:
                        # Just use the first one as a fallback
                        target_term = related_terms[0]
                        self._logger.debug(
                            "Multiple related terms found with no context "
                            "match, using first",
                            source_term=domain_term.term,
                            related_term=related_term_name,
                            contexts=domain_term.contexts,
                            target_id=target_term.id,
                        )

                sql_term.related_terms.append(target_term)
                self._logger.debug(
                    "Adding glossary term relationship",
                    source_term=sql_term.term,
                    target_term=target_term.term,
                    target_id=target_term.id,
                    source_id=sql_term.id,
                )

    async def _add_es_translations(
        self,
        term_map: dict[TermMapKey, TermMapValue],
        es_translations: list[GlossaryDefEs],
    ) -> None:
        """Add Spanish translations to the terms in the database."""
        for es_term in es_translations:
            # Find the corresponding term in the term map
            matching_terms = [
                v.sql_term
                for k, v in term_map.items()
                if k.term == es_term.term
            ]
            if not matching_terms:
                self._logger.warning(
                    "English term for Spanish translation not found",
                    term=es_term.term,
                    contexts=es_term.contexts,
                )
                continue

            if len(matching_terms) == 1:
                matching_term = matching_terms[0]
            else:
                # Get the term with the most matching contexts. If a tie,
                # default to the first one.
                matching_term = max(
                    matching_terms,
                    key=lambda t: len(
                        set(t.contexts).intersection(es_term.contexts)
                    ),
                )
                self._logger.info(
                    "Multiple matching terms found for Spanish "
                    "translation, using the one with the most "
                    "matching contexts",
                    term=es_term.term,
                    contexts=es_term.contexts,
                    matching_term=matching_term.term,
                )

            matching_term.definition_es = es_term.definition


@dataclass(frozen=True)
class TermMapKey:
    """Key for a glossary term on insert."""

    term: str
    """The glossary term."""

    contexts: tuple[str, ...]
    """The contexts of the glossary term."""

    def __hash__(self) -> int:
        return hash((self.term, self.contexts))


@dataclass(frozen=True)
class TermMapValue:
    """Value for a glossary term mapping on insert."""

    sql_term: SqlTerm
    """The SQL term object."""

    domain_term: GlossaryDef
    """The domain term object."""
