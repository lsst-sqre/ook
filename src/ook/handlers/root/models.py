"""Models for the external handler."""

from __future__ import annotations

from pydantic import AnyHttpUrl, BaseModel, Field
from safir.metadata import Metadata as SafirMetadata

__all__ = [
    "IndexResponse",
]


class IndexResponse(BaseModel):
    """Metadata returned by the external root URL of the application."""

    metadata: SafirMetadata = Field(..., title="Package metadata")

    api_docs: AnyHttpUrl = Field(..., title="API documentation URL")
