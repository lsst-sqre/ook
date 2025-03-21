"""Domain models for SDM schemas."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SdmColumn",
    "SdmSchema",
    "SdmSchemaRecursive",
    "SdmTable",
    "SdmTableRecursive",
]


@dataclass(kw_only=True, slots=True)
class SdmSchema:
    """A schema in an SDM database."""

    name: str
    """The name of the schema."""

    felis_id: str
    """The Felis ID (``@id``) of the schema."""

    description: str | None
    """A description of the schema."""

    github_owner: str
    """The owner of the GitHub repository containing the schema."""

    github_repo: str
    """The name of the GitHub repository containing the schema."""

    github_ref: str
    """The Git reference for the schema release."""

    github_path: str
    """The path to the schema file in the repository."""


@dataclass(kw_only=True, slots=True)
class SdmSchemaRecursive(SdmSchema):
    """A schema in an SDM database with included tables and columns."""

    tables: list[SdmTableRecursive]
    """The tables in the schema."""


@dataclass(kw_only=True, slots=True)
class SdmTable:
    """A table in an SDM schema."""

    name: str
    """The name of the table."""

    felis_id: str
    """The Felis ID (``@id``) of the table."""

    schema_name: str
    """The name of the schema the table belongs to."""

    description: str | None
    """A description of the table."""

    tap_table_index: int | None
    """The index of the table in the TAP schema presentation."""


@dataclass(kw_only=True, slots=True)
class SdmTableRecursive(SdmTable):
    """A table in an SDM schema with columns included."""

    columns: list[SdmColumn]
    """The columns in the table."""


@dataclass(kw_only=True, slots=True)
class SdmColumn:
    """A column in an SDM table."""

    name: str
    """The name of the column."""

    felis_id: str
    """The Felis ID (``@id``) of the column."""

    table_name: str
    """The name of the table the column belongs to."""

    schema_name: str
    """The name of the schema the column belongs to."""

    description: str | None
    """A description of the column."""

    datatype: str
    """The data type of the column."""

    ivoa_ucd: str | None
    """The IVOA UCD for the column."""

    ivoa_unit: str | None
    """The IVOA unit for the column."""

    tap_column_index: int | None
    """The index of the column in the TAP schema presentation."""
