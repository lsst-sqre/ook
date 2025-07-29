"""Storage-oriented models for direct SQLAlchemy mapping."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

__all__ = ["ResourcePaginationModel"]


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
