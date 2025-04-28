"""Storage interface to the terms table in the database."""

from __future__ import annotations

from dataclasses import dataclass

from safir.datetime import current_datetime
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_scoped_session
from structlog.stdlib import BoundLogger

from ook.dbschema.glossary import SqlTerm
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

    async def _associate_related_terms(  # noqa: C901 PLR0912
        self, term_map: dict[TermMapKey, TermMapValue]
    ) -> None:
        """Associate related terms in the database."""
        # Track relationships by ID to prevent duplicates
        established_relationships: set[tuple[int, int]] = set()

        for term_map_value in term_map.values():
            domain_term = term_map_value.domain_term
            sql_term = term_map_value.sql_term

            if not domain_term.related_terms:
                continue

            self._logger.debug(
                "Associating related terms",
                source_term=domain_term.term,
                contexts=domain_term.contexts,
                related_terms=domain_term.related_terms,
            )

            for related_term_name in domain_term.related_terms:
                # Get all rows with the same term name
                related_terms = [
                    v.sql_term
                    for k, v in term_map.items()
                    if k.term == related_term_name
                ]

                if not related_terms:
                    self._logger.warning(
                        "Related term not found in database",
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
                        self._logger.info(
                            "Multiple related terms found with no context "
                            "match, using first",
                            source_term=domain_term.term,
                            related_term=related_term_name,
                            contexts=domain_term.contexts,
                        )

                # Check if this relationship already exists by ID
                if target_term is not None:
                    relationship_key = (sql_term.id, target_term.id)
                    if relationship_key not in established_relationships:
                        sql_term.related_terms.append(target_term)
                        established_relationships.add(relationship_key)
                    else:
                        self._logger.debug(
                            "Skipping duplicate relationship",
                            source_term=sql_term.term,
                            source_id=sql_term.id,
                            target_term=target_term.term,
                            target_id=target_term.id,
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
