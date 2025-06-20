"""Database models for the Observatory glossary."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Table,
    UnicodeText,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

__all__ = ["SqlTerm", "term_relationships"]


term_relationships = Table(
    "term_relationships",
    Base.metadata,
    Column(
        "source_term_id",
        Integer,
        ForeignKey("term.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "related_term_id",
        Integer,
        ForeignKey("term.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)
"""An association table for the many-to-many relationship between terms."""


class SqlTerm(Base):
    """A SQLAlchemy model for glossary terms.

    Terms are unique through a combination of their term label and their
    definitions. This allows the same term to have multiple definitions.
    """

    __tablename__ = "term"

    __table_args__ = (
        UniqueConstraint(
            "term",
            "definition",
            name="uq_term_definition",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    """The primary key."""

    term: Mapped[str] = mapped_column(UnicodeText, nullable=False, index=True)
    """The glossary term (unicode)."""

    definition: Mapped[str] = mapped_column(
        UnicodeText, nullable=False, index=True
    )
    """The glossary term definition in English (unicode)."""

    definition_es: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True
    )
    """The glossary term definition in Spanish (unicode)."""

    is_abbr: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    """Whether the term is an abbreviation."""

    contexts: Mapped[list[str]] = mapped_column(
        ARRAY(UnicodeText),
        index=True,
        nullable=False,
        default=[],
    )
    """The contexts of the glossary term.

    Note: Even though we use a list here, order doesn't matter for uniqueness.
    The application should sort contexts before storing to ensure consistent
    uniqueness checks.
    """

    related_documentation: Mapped[list[str]] = mapped_column(
        ARRAY(UnicodeText), nullable=False, default=[]
    )
    """The related documentation for the glossary term.

    These relationships are provided through lsst/lsst-texmf's
    glossarydefs.csv and aren't database relationships. Items may be
    URLs, DOI links, or other internal document handles.
    """

    # Self-referential relationship for related terms
    related_terms: Mapped[list[SqlTerm]] = relationship(
        "SqlTerm",
        secondary=term_relationships,
        primaryjoin=(id == term_relationships.c.source_term_id),
        secondaryjoin=(id == term_relationships.c.related_term_id),
        collection_class=list,
        # Delete row in related_terms table, but not the
        # related terms themselves
        cascade="all",
    )
    """Other glossary terms that are related to this term."""

    referenced_by: Mapped[list[SqlTerm]] = relationship(
        "SqlTerm",
        secondary=term_relationships,
        primaryjoin=(id == term_relationships.c.related_term_id),
        secondaryjoin=(id == term_relationships.c.source_term_id),
        overlaps="related_terms",
        viewonly=True,
    )
    """Glossary terms that reference this term."""

    date_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """The date this record was last updated."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if "contexts" in kwargs:
            # Sort contexts to ensure consistent uniqueness check
            kwargs["contexts"] = sorted(kwargs["contexts"])
        super().__init__(*args, **kwargs)
