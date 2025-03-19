"""Domain models for documentation deep links."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SdmSchemaLink"]


@dataclass(slots=True)
class SdmSchemaLink:
    """A link to top-level SDM schema documentation."""

    name: str
    """The name of the schema."""

    html_url: str
    """The URL to the schema's top-level documentation page."""

    kind: str = "schema browser"
    """The kind of documentation link."""

    source_title: str = "Science Data Model Schemas"
