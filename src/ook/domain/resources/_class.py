"""Resource class enumeration."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ResourceClass"]


class ResourceClass(StrEnum):
    """Enumeration of resource classes for metadata specialization.

    This is not to be confused with `ResourceType`, which maps to DataCite
    resource types. This enumeration is used to categorize resources into
    classes corresponding to specialized metadata tables.
    """

    generic = "generic"
    """A generic resource without specialized metadata."""

    document = "document"
    """A document resource, e.g. a technote or change-controlled document."""
