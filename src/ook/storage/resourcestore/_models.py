"""Storage-oriented models for direct SQLAlchemy mapping."""

from __future__ import annotations

from dataclasses import asdict, dataclass
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

    def asdict(self) -> dict[str, bool]:
        """Convert to a dictionary representation."""
        return asdict(self)


class ResourcePaginationModel(BaseModel):
    """Lightweight model for a resource that's only used internally by
    the datastore for paginating over resources.

    The `ResourceStore.get_resources` method uses this model for generating
    a paginated list of resources. But it converts these resources into their
    domain specific models before returning them to the caller.
    """

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
