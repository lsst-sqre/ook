"""Database models for SDM Schemas."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    UnicodeText,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .links import SqlSdmColumnLink, SqlSdmSchemaLink, SqlSdmTableLink

__all__ = ["SqlSdmColumn", "SqlSdmSchema", "SqlSdmTable"]


class SqlSdmSchema(Base):
    """A SQLAlchemy model for an SDM schema.

    We currently assume that SDM models are stored in a GitHub repository
    as YAML files managed by Felis (https://felis.lsst.io).
    """

    __tablename__ = "sdm_schemas"

    __table_args__ = (UniqueConstraint("name", name="uq_sdm_schema_name"),)

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    name: Mapped[str] = mapped_column(UnicodeText, nullable=False, index=True)
    """The name of the schema."""

    felis_id: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The Felis ID of the schema."""

    description: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """A description of the schema."""

    github_owner: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The owner of the GitHub repository hosting the schema."""

    github_repo: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The name of the GitHub repository hosting the schema."""

    github_ref: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The Git reference for the schema release."""

    github_path: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The path to the schema file in the repository."""

    date_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """The date this record was last updated."""

    tables: Mapped[list[SqlSdmTable]] = relationship(
        "SqlSdmTable", back_populates="schema"
    )
    """The tables that belong to this schema."""

    links: Mapped[list[SqlSdmSchemaLink]] = relationship(
        "SqlSdmSchemaLink", back_populates="schema"
    )
    """The links to the schema in documentation."""


class SqlSdmTable(Base):
    """A SQLAlchemy model for a table in an SDM schema."""

    __tablename__ = "sdm_tables"

    __table_args__ = (
        UniqueConstraint("schema_id", "name", name="uq_sdm_table_schema_name"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    schema_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sdm_schemas.id"), nullable=False, index=True
    )
    """The ID of the schema to which this table belongs."""

    name: Mapped[str] = mapped_column(UnicodeText, nullable=False, index=True)
    """The name of the table."""

    felis_id: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The Felis ID of the table."""

    description: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """A description of the table."""

    tap_table_index: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    """The index of the table in the TAP schema."""

    date_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """The date this record was last updated."""

    schema: Mapped[SqlSdmSchema] = relationship(
        "SqlSdmSchema", back_populates="tables"
    )
    """The schema to which this table belongs."""

    columns: Mapped[list[SqlSdmColumn]] = relationship(
        "SqlSdmColumn", back_populates="table"
    )
    """The columns that belong to this table."""

    links: Mapped[list[SqlSdmTableLink]] = relationship(
        "SqlSdmTableLink", back_populates="table"
    )
    """The links to the table in documentation."""


class SqlSdmColumn(Base):
    """A SQLAlchemy model for a column in an SDM table."""

    __tablename__ = "sdm_columns"

    __table_args__ = (
        UniqueConstraint("table_id", "name", name="uq_sdm_column_table_name"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    table_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sdm_tables.id"), nullable=False, index=True
    )
    """The ID of the table to which this column belongs."""

    name: Mapped[str] = mapped_column(UnicodeText, nullable=False, index=True)
    """The name of the column."""

    felis_id: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The Felis ID of the column."""

    description: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """A description of the column."""

    datatype: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The data type of the column."""

    ivoa_ucd: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """The IVOA UCD for the column."""

    ivoa_unit: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """The IVOA unit for the column."""

    tap_column_index: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )

    date_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """The date this record was last updated."""

    table: Mapped[SqlSdmTable] = relationship(
        "SqlSdmTable", back_populates="columns"
    )
    """The table to which this column belongs."""

    links: Mapped[list[SqlSdmColumnLink]] = relationship(
        "SqlSdmColumnLink", back_populates="column"
    )
    """The links to the column in documentation."""
