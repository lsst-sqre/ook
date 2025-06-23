"""Database model for SDM Schema Links."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, UnicodeText
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .sdmschemas import SqlSdmColumn, SqlSdmSchema, SqlSdmTable

__all__ = [
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
