"""Domain models for documentation deep links."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Link",
    "LinksCollection",
    "SdmColumnLink",
    "SdmColumnLinksCollection",
    "SdmSchemaLink",
    "SdmSchemaLinksCollection",
    "SdmTableLink",
    "SdmTableLinksCollection",
]


@dataclass(slots=True, kw_only=True)
class Link:
    """A link to documentation."""

    html_url: str
    """The URL to the documentation page."""

    title: str
    """The title of the documentation."""

    type: str
    """The type of documentation."""

    collection_title: str | None = None
    """The title of the collection of documentation this link refers to."""


@dataclass(slots=True, kw_only=True)
class SdmSchemaLink(Link):
    """A link to an SDM schema's documentation."""

    name: str
    """The name of the schema."""


@dataclass(slots=True, kw_only=True)
class SdmTableLink(Link):
    """A link to an SDM table's documentation."""

    schema_name: str
    """The name of the schema."""

    name: str
    """The name of the table."""


@dataclass(slots=True, kw_only=True)
class SdmColumnLink(Link):
    """A link to an SDM column's documentation."""

    schema_name: str
    """The name of the schema."""

    table_name: str
    """The name of the table."""

    name: str
    """The name of the column."""


@dataclass(slots=True, kw_only=True)
class LinksCollection[T: Link]:
    """A collection of links to documentation of a specific entity."""

    links: list[T]
    """The documentation links."""


@dataclass(slots=True, kw_only=True)
class SdmSchemaLinksCollection(LinksCollection[SdmSchemaLink]):
    """A collection of links to an SDM schema."""

    schema_name: str
    """The name of the schema."""


@dataclass(slots=True, kw_only=True)
class SdmTableLinksCollection(LinksCollection[SdmTableLink]):
    """A collection of links to an SDM table."""

    schema_name: str
    """The name of the schema."""

    table_name: str
    """The name of the table."""


@dataclass(slots=True, kw_only=True)
class SdmColumnLinksCollection(LinksCollection[SdmColumnLink]):
    """A collection of links to SDM columns."""

    schema_name: str
    """The name of the schema."""

    table_name: str
    """The name of the table."""

    column_name: str
    """The name of the column."""
