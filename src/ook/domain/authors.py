"""Domain model for authors and affiliations."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["Address", "Affiliation", "Author", "Collaboration"]


class Address(BaseModel):
    """An address for an affiliation."""

    street: str | None = Field(
        default=None, description="Street address of the affiliation."
    )

    city: str | None = Field(
        default=None, description="City/town of the affiliation."
    )

    state: str | None = Field(
        default=None, description="State or province of the affiliation."
    )

    postal_code: str | None = Field(
        default=None, description="Postal code of the affiliation."
    )

    country: str | None = Field(
        default=None, description="Country of the affiliation."
    )


class Affiliation(BaseModel):
    """An affiliation."""

    name: str = Field(description="Organization name.")

    department: str | None = Field(
        default=None, description="Department within the organization."
    )

    internal_id: str = Field(
        description="Internal ID of the affiliation.",
    )

    address: Address | None = Field(
        default=None, description="Address of the affiliation."
    )

    email: str | None = Field(
        default=None,
        description="Email domain of the affiliation (e.g., 'example.edu').",
    )

    ror_id: str | None = Field(
        default=None,
        description="ROR ID of the affiliation (without ror.org domain).",
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
