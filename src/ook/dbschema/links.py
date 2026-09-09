"""Database models for documentation links.

Every link is a row in the polymorphic ``link`` table, and each link
domain adds a joined-table subtype naming the entity the link points at.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, UnicodeText
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .intersphinxentities import SqlIntersphinxEntity
    from .intersphinxsources import SqlIntersphinxSource
    from .sdmschemas import SqlSdmColumn, SqlSdmSchema, SqlSdmTable

__all__ = [
    "SqlIntersphinxLink",
    "SqlLink",
    "SqlSdmColumnLink",
    "SqlSdmSchemaLink",
    "SqlSdmTableLink",
]


class SqlLink(Base):
    """A SQLAlchemy model for documentation links."""

    __tablename__ = "link"

    __mapper_args__ = {  # noqa: RUF012
        "polymorphic_identity": "link",
        "polymorphic_on": "type",
    }

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    type: Mapped[str]
    """The descriminator for link subclasses."""

    html_url: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The URL to the schema's top-level documentation page."""

    source_type: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The type of documentation this link refers to."""

    source_title: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The title of the documentation this link refers to."""

    source_collection_title: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True
    )
    """The title of the collection of documentation this link refers to.

    For example, this field refers to the title of the user guide while
    `source_title` refers to the title of a specific page in the user guide.
    """

    date_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """The date this record was last updated."""


class SqlSdmSchemaLink(SqlLink):
    """A SQLAlchemy model for links to top-level schema documentation."""

    __tablename__ = "links_sdm_schemas"

    __mapper_args__ = {  # noqa: RUF012
        "polymorphic_identity": "sdm_schema",
    }

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("link.id"), primary_key=True
    )
    """The primary key."""

    schema_id: Mapped[BigInteger] = mapped_column(
        BigInteger,
        ForeignKey("sdm_schema.id"),
        nullable=False,
        index=True,
    )
    """The ID of the schema to which the link belongs."""

    schema: Mapped[SqlSdmSchema] = relationship(
        "SqlSdmSchema", back_populates="links"
    )
    """The schema this link belongs to."""


class SqlSdmTableLink(SqlLink):
    """A SQLAlchemy model for links to table documentation."""

    __tablename__ = "links_sdm_tables"

    __mapper_args__ = {  # noqa: RUF012
        "polymorphic_identity": "sdm_table",
    }

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("link.id"), primary_key=True
    )
    """The primary key."""

    table_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sdm_table.id"),
        nullable=False,
        index=True,
    )
    """The ID of the table to which the link belongs."""

    table: Mapped[SqlSdmTable] = relationship(
        "SqlSdmTable", back_populates="links"
    )
    """The table this link belongs to."""


class SqlSdmColumnLink(SqlLink):
    """A SQLAlchemy model for links to column documentation."""

    __tablename__ = "links_sdm_columns"

    __mapper_args__ = {  # noqa: RUF012
        "polymorphic_identity": "sdm_column",
    }

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("link.id"), primary_key=True
    )
    """The primary key."""

    column_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sdm_column.id"),
        nullable=False,
        index=True,
    )
    """The ID of the column to which this link belongs."""

    column: Mapped[SqlSdmColumn] = relationship(
        "SqlSdmColumn", back_populates="links"
    )
    """The SDM column this link belongs to."""


class SqlIntersphinxLink(SqlLink):
    """A SQLAlchemy model for a link to a Sphinx-documented object.

    Two foreign keys, because an intersphinx link is a statement about a
    pair: this documentation site documents this object. The entity FK is
    what a reader's query resolves against, and the source FK is what makes
    a re-ingest able to replace exactly one site's links and leave every
    other site's alone.
    """

    __tablename__ = "links_intersphinx"

    __mapper_args__ = {  # noqa: RUF012
        "polymorphic_identity": "intersphinx",
    }

    id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("link.id", ondelete="CASCADE"), primary_key=True
    )
    """The primary key, shared with the ``link`` row this subtype extends.

    ``ON DELETE CASCADE`` so the per-source replace in the ingest path can
    delete the base ``link`` rows in one statement without orphaning their
    subtype rows.
    """

    entity_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("intersphinx_entity.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    """The ID of the entity this link documents."""

    source_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("intersphinx_source.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    """The ID of the registered source this link was ingested from."""

    entity: Mapped[SqlIntersphinxEntity] = relationship(
        "SqlIntersphinxEntity", back_populates="links"
    )
    """The entity this link documents."""

    source: Mapped[SqlIntersphinxSource] = relationship(
        "SqlIntersphinxSource", back_populates="links"
    )
    """The registered source this link was ingested from."""
