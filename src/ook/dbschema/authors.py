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
]


class SqlAuthor(Base):
    """A SQLAlchemy model for authors."""

    __tablename__ = "author"

    __table_args__ = (UniqueConstraint("orcid", name="uq_author_orcid"),)

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

    orcid: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """The ORCID of the author.

    This is meant to be the ORCID identifier without its orcid.org domain.
    For example, 0000-0003-3001-676X for https://orcid.org/0000-0003-3001-676X
    This should be converted back to the full URL form when presenting
    data to the user.
    """

    date_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """The date this record was last updated."""


class SqlAffiliation(Base):
    """A SQLAlchemy model for affiliations."""

    __tablename__ = "affiliation"

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

    department: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """The department of the affiliation (unicode)."""

    email_domain: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True
    )
    """The email domain of the affiliation (e.g., 'example.edu')."""

    ror_id: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """The ROR ID of the affiliation.

    This is meant to be the ROR identifier without its ror.org domain.
    For example, 048g3cy84 for https://ror.org/048g3cy84 (Rubin Observatory).
    This should be converted back to the full URL form when presenting
    data to the user.
    """

    address_street: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True
    )
    """The street address of the affiliation (unicode)."""

    address_city: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True
    )
    """The city of the affiliation (unicode)."""

    address_state: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True
    )
    """The state of the affiliation (unicode)."""

    address_postal_code: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True
    )
    """The postal code of the affiliation (unicode)."""

    address_country: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True
    )
    """The country code of the affiliation (unicode)."""

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
        ForeignKey("author.id"), primary_key=True
    )

    affiliation_id: Mapped[int] = mapped_column(
        ForeignKey("affiliation.id"), primary_key=True
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False)

    author: Mapped[SqlAuthor] = relationship(
        "SqlAuthor", back_populates="affiliations"
    )

    affiliation: Mapped[SqlAffiliation] = relationship(
        "SqlAffiliation", back_populates="authors"
    )
