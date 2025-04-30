"""Database models for authors and their affiliatinons."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    UnicodeText,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

__all__ = [
    "SqlAffiliation",
    "SqlAuthor",
    "SqlAuthorAffiliation",
    "SqlCollaboration",
]


class SqlAuthor(Base):
    """A SQLAlchemy model for authors."""

    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    internal_id: Mapped[str] = mapped_column(
        UnicodeText, nullable=False, index=True, unique=True
    )
    """The internal ID of the author.

    Ths is the key for the author in lsst-texmf's authordb.yaml file.
    """

    surname: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=False, index=True
    )
    """The surname of the author (unicode)."""

    given_name: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True, index=True
    )
    """The given name of the author (unicode)."""

    affiliations: Mapped[list[SqlAuthorAffiliation]] = relationship(
        "SqlAuthorAffiliation",
        back_populates="author",
        cascade="all, delete-orphan",
        order_by="SqlAuthorAffiliation.position",
    )
    """The author's affiliations."""

    notes: Mapped[list[str]] = mapped_column(
        ARRAY(UnicodeText), nullable=False, default=list
    )
    """Notes (used as the ``altaffiliation`` in AASTeX)."""

    email: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """The email address of the author."""

    orcid: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True, unique=True
    )
    """The ORCID of the author."""

    date_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """The date this record was last updated."""


class SqlCollaboration(Base):
    """A SQLAlchemy model for collaborations."""

    __tablename__ = "collaborations"

    __table_args__ = (
        UniqueConstraint(
            "internal_id", "name", name="uq_collaboration_internal_id_name"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    internal_id: Mapped[str] = mapped_column(
        UnicodeText, nullable=False, index=True, unique=True
    )
    """The internal ID of the collaboration.

    This is the key for the collaboration in lsst-texmf's authordb.yaml file.
    """

    name: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=False, index=True
    )
    """The name of the collaboration (unicode)."""

    date_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """The date this record was last updated."""

    # TODO(jonathansick): consider adding an ordered many-to-many
    # relationship to authors, but this is not needed for now.


class SqlAffiliation(Base):
    """A SQLAlchemy model for affiliations."""

    __tablename__ = "affiliations"

    __table_args__ = (
        UniqueConstraint(
            "internal_id", "name", name="uq_affiliation_internal_id_name"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    internal_id: Mapped[str] = mapped_column(
        UnicodeText, nullable=False, index=True, unique=True
    )
    """The internal ID of the affiliation.

    This is the key for the affiliation in lsst-texmf's authordb.yaml file.
    """

    name: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=False, index=True
    )
    """The name of the affiliation (unicode)."""

    address: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True, index=True
    )
    """The address of the affiliation (unicode)."""

    authors: Mapped[list[SqlAuthorAffiliation]] = relationship(
        "SqlAuthorAffiliation",
        back_populates="affiliation",
        cascade="all, delete-orphan",
    )
    """The authors with this affiliation."""

    date_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """The date this record was last updated."""


class SqlAuthorAffiliation(Base):
    """A SQLAlchemy model for the many-to-many relationship between authors
    and affiliations.
    """

    __tablename__ = "author_affiliations"

    author_id: Mapped[int] = mapped_column(
        ForeignKey("authors.id"), primary_key=True
    )

    affiliation_id: Mapped[int] = mapped_column(
        ForeignKey("affiliations.id"), primary_key=True
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False)

    author: Mapped[SqlAuthor] = relationship(
        "SqlAuthor", back_populates="affiliations"
    )

    affiliation: Mapped[SqlAffiliation] = relationship(
        "SqlAffiliation", back_populates="authors"
    )
