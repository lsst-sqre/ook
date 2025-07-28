"""Database schema for Ook resources."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    UnicodeText,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .authors import SqlAuthor


__all__ = [
    "SqlContributor",
    "SqlDocumentResource",
    "SqlExternalReference",
    "SqlResource",
    "SqlResourceRelation",
]


class SqlResource(Base):
    """A SQLAlchemy model for resources."""

    __tablename__ = "resource"

    __mapper_args__ = {  # noqa: RUF012
        "polymorphic_identity": "resource",
        "polymorphic_on": "resource_class",
    }

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    """The primary key (the Base32 ID as an integer)."""

    resource_class: Mapped[str | None]
    """The descriminator for resource subclasses."""

    date_created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """The date when the DB row was created."""

    date_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """The date when the DB row was last updated."""

    title: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The title of the resource."""

    description: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """A description of the resource."""

    url: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """A URL pointing to the resource."""

    doi: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True, unique=True
    )
    """The DOI of the resource, if it has one."""

    date_resource_published: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """The date when the resource was first published (if applicable)."""

    date_resource_updated: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """The date when the resource was last updated (if applicable)."""

    version: Mapped[str | None]
    """The version of the resource, if applicable."""

    type: Mapped[str | None]
    """The type of the resource (DataCite vocabulary)."""

    contributors: Mapped[list[SqlContributor]] = relationship(
        "SqlContributor",
        back_populates="resource",
        cascade="all, delete-orphan",
        order_by="SqlContributor.order",
    )
    """Contributors to the resource."""

    relations: Mapped[list[SqlResourceRelation]] = relationship(
        "SqlResourceRelation",
        back_populates="resource",
        cascade="all, delete-orphan",
        foreign_keys="SqlResourceRelation.source_resource_id",
    )
    """All relations to other resources (both internal and external)."""

    __table_args__ = (
        Index("idx_resource_class", "resource_class"),
        Index("idx_resource_date_published", "date_resource_published"),
        Index("idx_resource_date_updated", "date_resource_updated"),
    )


class SqlExternalReference(Base):
    """A SQLAlchemy model for external resources that are referenced by Ook
    resources.

    This model corresponds to the ExternalReference domain model and stores
    metadata about external resources that cannot be directly modeled as
    Ook resources.
    """

    __tablename__ = "external_reference"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    """Primary key."""

    url: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """URL of the external resource."""

    doi: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True, unique=True
    )
    """Digital Object Identifier (DOI) of the external resource."""

    arxiv_id: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True, unique=True
    )
    """arXiv identifier of the external resource."""

    isbn: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True, unique=True
    )
    """International Standard Book Number (ISBN) of the external resource."""

    issn: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True, unique=True
    )
    """International Standard Serial Number (ISSN) of the external resource."""

    ads_bibcode: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True, unique=True
    )
    """Astrophysics Data System (ADS) Bibcode of the external resource."""

    type: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """Type of the external resource (DataCite vocabulary resourceType)."""

    title: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """Title of the external resource."""

    publication_year: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True
    )
    """Year of publication."""

    volume: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """Volume of the external resource."""

    issue: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """Issue of the external resource."""

    number: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """Number of the external resource."""

    number_type: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """Type of number field (DataCite vocabulary numberType)."""

    first_page: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """First page of the external resource within the related item."""

    last_page: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """Last page of the external resource within the related item."""

    publisher: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """Publisher of the external resource."""

    edition: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """Edition of the external resource."""

    contributors: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    """List of contributors to the external resource stored as JSON.

    This field stores the serialized ExternalContributor objects including
    their names, affiliations, roles, and other metadata.
    """


class SqlContributor(Base):
    """A SQLAlchemy model for the many-to-many relationship between resources
    and contributors (i.e., authors).
    """

    __tablename__ = "contributor"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    resource_id: Mapped[int] = mapped_column(
        ForeignKey("resource.id"), nullable=False
    )
    """The resource ID."""

    order: Mapped[int] = mapped_column(Integer, nullable=False)
    """The order of the contributor in the list for a given role."""

    role: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The role of the contributor (DataCite vocabulary)."""

    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("author.id"), nullable=True
    )
    """The author ID (foreign key, not internal author ID)."""

    resource: Mapped[SqlResource] = relationship(
        "SqlResource", back_populates="contributors"
    )
    """The resource this contributor is associated with."""

    author: Mapped[SqlAuthor | None] = relationship("SqlAuthor")
    """The author."""

    __table_args__ = (
        UniqueConstraint(
            "resource_id",
            "order",
            "role",
            name="uq_contributor_resource_order_role",
        ),
    )


class SqlResourceRelation(Base):
    """A SQLAlchemy model for the many-to-many relationship between resources
    representing relations like citations, versions, etc.

    This model supports relations to both internal Ook resources and external
    references. Exactly one of related_resource_id or related_external_ref_id
    must be set.
    """

    __tablename__ = "resource_relation"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    source_resource_id: Mapped[int] = mapped_column(
        ForeignKey("resource.id"), nullable=False
    )
    """The source resource ID."""

    related_resource_id: Mapped[int | None] = mapped_column(
        ForeignKey("resource.id"), nullable=True
    )
    """The related resource ID (for internal Ook resources)."""

    related_external_ref_id: Mapped[int | None] = mapped_column(
        ForeignKey("external_reference.id"), nullable=True
    )
    """The related external reference ID (for external resources)."""

    relation_type: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The type of relation (DataCite vocabulary)."""

    resource: Mapped[SqlResource] = relationship(
        "SqlResource",
        back_populates="relations",
        foreign_keys=[source_resource_id],
    )
    """The source resource this relation belongs to."""

    related_resource: Mapped[SqlResource | None] = relationship(
        "SqlResource",
        foreign_keys=[related_resource_id],
    )
    """The related internal resource, if applicable."""

    related_external_reference: Mapped[SqlExternalReference | None] = (
        relationship(
            "SqlExternalReference",
            foreign_keys=[related_external_ref_id],
        )
    )
    """The related external reference, if applicable."""

    __table_args__ = (
        # Ensure exactly one of related_resource_id or related_external_ref_id
        # is set. This constraint will be enforced at the application level
        UniqueConstraint(
            "source_resource_id",
            "related_resource_id",
            "related_external_ref_id",
            "relation_type",
            name="uq_resource_relation",
        ),
        Index("idx_resource_relation_source", "source_resource_id"),
        Index("idx_resource_relation_type", "relation_type"),
    )


class SqlDocumentResource(SqlResource):
    """A SQLAlchemy model for document resources.

    This model represents documents like technotes and change-controlled
    documents with additional metadata specific to documents.
    """

    __tablename__ = "document_resource"

    __mapper_args__ = {  # noqa: RUF012
        "polymorphic_identity": "document",
    }

    id: Mapped[int] = mapped_column(
        ForeignKey("resource.id"), primary_key=True
    )
    """Foreign key to the parent resource table."""

    series: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """Series name of the document (e.g., "DMTN", "LDM")."""

    handle: Mapped[str] = mapped_column(
        UnicodeText, nullable=False, unique=True
    )
    """Handle identifier (e.g., "SQR-000")."""

    generator: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """Document generator used (e.g., "Documenteer 2.0.0", "Lander 2.0.0")."""
