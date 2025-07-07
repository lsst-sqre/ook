"""API models."""

from __future__ import annotations

from typing import Annotated, Any, Self

from pydantic import BaseModel, BeforeValidator, Field, HttpUrl

from ook.domain.authors import Author as AuthorDomain

__all__ = ["Author"]


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


def format_ror_url(value: Any) -> str | None:
    """Convert ROR identifier to a URL if necessary."""
    if value is None:
        return None
    if not str(value).startswith("http"):
        # Format it as a URL by adding the ror.org domain
        ror_id = str(value).lstrip("/")
        return f"https://ror.org/{ror_id}"
    return value


def format_orcid_url(value: Any) -> str | None:
    """Convert ORCID identifier to a URL if necessary."""
    if value is None:
        return None
    if not str(value).startswith("http"):
        # Format it as a URL by adding the orcid.org domain
        orcid_id = str(value).lstrip("/")
        return f"https://orcid.org/{orcid_id}"
    return value


class Affiliation(BaseModel):
    """An affiliation."""

    name: str = Field(description="Name of the affiliation.")

    department: str | None = Field(
        default=None, description="Department within the organization."
    )

    internal_id: str = Field(
        description="Internal ID of the affiliation.",
    )

    ror: Annotated[str | None, BeforeValidator(format_ror_url)] = Field(
        default=None,
        description="ROR URL of the affiliation.",
    )

    address: Address | None = Field(
        default=None, description="Address of the affiliation."
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

    orcid: Annotated[HttpUrl | None, BeforeValidator(format_orcid_url)] = (
        Field(
            default=None,
            description="ORCID of the author (URL), or null if not available.",
        )
    )

    notes: list[str] = Field(
        default_factory=list,
        description="Notes about the author.",
    )

    affiliations: list[Affiliation] = Field(
        default_factory=list,
        description="The author's affiliations.",
    )

    @classmethod
    def from_domain(cls, author: AuthorDomain) -> Self:
        """Create an AuthorResponse from a domain Author."""
        return cls.model_validate(author, from_attributes=True)
