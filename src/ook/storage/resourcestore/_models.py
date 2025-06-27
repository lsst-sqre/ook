"""Storage-oriented models for direct SQLAlchemy mapping."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict

__all__ = ["ResourceLoadOptions"]


@dataclass
class ResourceLoadOptions:
    """Options for controlling what related data to load with a Resource."""

    include_contributors: bool = False
    """Whether to load contributor data."""

    include_resource_relations: bool = False
    """Whether to load relations to other internal resources."""

    include_external_relations: bool = False
    """Whether to load relations to external resources."""

    @classmethod
    def all(cls) -> ResourceLoadOptions:
        """Load all related data."""
        return cls(
            include_contributors=True,
            include_resource_relations=True,
            include_external_relations=True,
        )

    @classmethod
    def none(cls) -> ResourceLoadOptions:
        """Load no related data (default for REST API)."""
        return cls()

    @classmethod
    def contributors_only(cls) -> ResourceLoadOptions:
        """Load only contributor data."""
        return cls(include_contributors=True)

    @classmethod
    def relations_only(cls) -> ResourceLoadOptions:
        """Load only relationship data."""
        return cls(
            include_resource_relations=True,
            include_external_relations=True,
        )


class ResourcePaginationModel(BaseModel):
    """Lightweight model for pagination of resources."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    """The resource ID."""

    resource_class: str | None = None
    """The resource class."""

    date_created: datetime
    """Creation date."""

    date_updated: datetime
    """Last update date."""

    title: str
    """The resource title."""
