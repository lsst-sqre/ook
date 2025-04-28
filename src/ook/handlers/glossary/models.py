"""API models."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, Field

from ook.domain.glossary import GlossaryTerm

__all__ = ["SearchedTerm", "Term"]


class Term(BaseModel):
    """A glossary term."""

    term: Annotated[str, Field(description="The glossary term.")]

    definition: Annotated[
        str, Field(description="The definition of the glossary term.")
    ]

    related_documentation: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="The related documentation tags.",
        ),
    ] = []

    is_abbr: Annotated[
        bool,
        Field(
            description="Whether the term is an abbreviation.",
        ),
    ] = False

    contexts: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="The contexts where the definition is relevant.",
        ),
    ]

    @classmethod
    def from_domain(cls, term: GlossaryTerm) -> Self:
        """Create a Term response model from a domain model."""
        return cls(
            term=term.term,
            definition=term.definition,
            related_documentation=term.related_documentation,
            is_abbr=term.is_abbr,
            contexts=term.contexts,
        )


class SearchedTerm(Term):
    """A glossary term with search relevance metadata."""

    relevance: Annotated[
        float,
        Field(
            description=("The relevance of the term to the search term."),
        ),
    ]

    @classmethod
    def from_domain(cls, term: GlossaryTerm) -> Self:
        """Create a SearchedTerm response from a domain model.

        The ``relevance`` field is required for this response model.
        """
        if term.relevance is None:
            raise ValueError("Term relevance is not set.")
        return cls(
            term=term.term,
            definition=term.definition,
            related_documentation=term.related_documentation,
            is_abbr=term.is_abbr,
            relevance=term.relevance,
            contexts=term.contexts,
        )
