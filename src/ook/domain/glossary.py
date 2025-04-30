"""The glossary domain model."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

__all__ = ["GlossaryTerm"]


class GlossaryTerm(BaseModel):
    """A domain model for a glossary term."""

    term: Annotated[
        str,
        Field(
            description="The glossary term.",
        ),
    ]

    definition: Annotated[
        str,
        Field(
            description="The definition of the glossary term.",
        ),
    ]

    definition_es: Annotated[
        str | None,
        Field(
            description="The definition of the glossary term in Spanish.",
        ),
    ] = None

    contexts: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="The contexts of the glossary term.",
        ),
    ]

    is_abbr: Annotated[
        bool,
        Field(
            description="Whether the term is an abbreviation.",
        ),
    ] = False

    related_documentation: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="The related documentation tags.",
        ),
    ]

    related_terms: Annotated[
        list[GlossaryTerm],
        Field(
            default_factory=list,
            description="The related terms.",
        ),
    ]

    referenced_by: Annotated[
        list[GlossaryTerm],
        Field(
            default_factory=list,
            description="The terms that reference this term.",
        ),
    ]

    relevance: Annotated[
        float | None,
        Field(
            description=(
                "The relevance of the term to the search term. "
                "A value between 0 and 100."
            ),
        ),
    ] = None
