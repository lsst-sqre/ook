"""Database model for SDM Schema Links."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Unicode,
    UnicodeText,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

__all__ = ["SqlSdmColumnLink", "SqlSdmSchemaLink", "SqlSdmTableLink"]


class SqlSdmSchemaLink(Base):
    """A SQLAlchemy model for links to top-level schema documentation."""

    __tablename__ = "links_sdm_schemas"

    __table_args__ = (UniqueConstraint("name", name="uq_sdm_schema_name"),)

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    name: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The name of the schema."""

    html_url: Mapped[str] = mapped_column(Unicode, nullable=False)
    """The URL to the schema's top-level documentation page."""

    date_updated: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    """The date this record was last updated."""

    tables: Mapped[list[SqlSdmTableLink]] = relationship(
        "SqlSdmTableLink", back_populates="schema"
    )
    """The tables that belong to this schema."""


class SqlSdmTableLink(Base):
    """A SQLAlchemy model for links to table documentation."""

    __tablename__ = "links_sdm_tables"

    __table_args__ = (
        UniqueConstraint("schema_id", "name", name="uq_sdm_table_schema_name"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    schema_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("links_sdm_schemas.id"),
        nullable=False,
        index=True,
    )
    """The ID of the schema to which the table belongs."""

    schema: Mapped[SqlSdmSchemaLink] = relationship(
        "SqlSdmSchemaLink", back_populates="tables"
    )
    """The schema this table belongs to."""

    name: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The name of the table."""

    html_url: Mapped[str] = mapped_column(Unicode, nullable=False)
    """The URL to the table's documentation page."""

    date_updated: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    """The date this record was last updated."""

    columns: Mapped[list[SqlSdmColumnLink]] = relationship(
        "SqlSdmColumnLink", back_populates="table"
    )
    """The columns that belong to this table."""


class SqlSdmColumnLink(Base):
    """A SQLAlchemy model for links to column documentation."""

    __tablename__ = "links_sdm_columns"

    __table_args__ = (
        UniqueConstraint("table_id", "name", name="uq_sdm_column_table_name"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    table_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("links_sdm_tables.id"),
        nullable=False,
        index=True,
    )
    """The ID of the table to which the column belongs."""

    table: Mapped[SqlSdmTableLink] = relationship(
        "SqlSdmTableLink", back_populates="columns"
    )
    """The table this column belongs to."""

    name: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The name of the column."""

    html_url: Mapped[str] = mapped_column(Unicode, nullable=False)
    """The URL to the column's documentation page."""

    date_updated: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    """The date this record was last updated."""
