"""Domain model for authors and affiliations."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["Affiliation", "Author", "Collaboration"]


class Affiliation(BaseModel):
    """An affiliation."""

    name: str = Field(description="Name of the affiliation.")

    internal_id: str = Field(
        description="Internal ID of the affiliation.",
    )

    address: str | None = Field(
        default=None, description="Address of the affiliation."
    )

    email: str | None = Field(
        default=None,
        description="Email domain of the affiliation (e.g., 'example.edu').",
    )


class Collaboration(BaseModel):
    """A collaboration."""

    name: str = Field(description="Name of the collaboration.")

    internal_id: str = Field(
        description="Internal ID of the collaboration.",
    )


class Author(BaseModel):
    """An author."""

    internal_id: str = Field(
        description="Internal ID of the author.",
    )

    surname: str = Field(description="Surname of the author.")

    given_name: str | None = Field(
        description="Given name of the author.",
    )

    orcid: str | None = Field(
        default=None,
        description="ORCID of the author (the identifier, not the URL).",
    )

    email: str | None = Field(
        default=None,
        description="Email address of the author.",
    )

    notes: list[str] = Field(
        default_factory=list,
        description="Notes about the author.",
    )

    affiliations: list[Affiliation] = Field(
        default_factory=list,
        description="Affiliations of the author.",
    )
