"""Domain model for authors and affiliations."""

from __future__ import annotations

from functools import lru_cache

import pycountry
from pydantic import BaseModel, Field

__all__ = ["Address", "Affiliation", "Author", "AuthorSearchResult"]


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

    country_code: str | None = Field(
        default=None, description="ISO 3166-1 alpha-2 country code."
    )

    country_name: str | None = Field(
        default=None,
        description="Legacy country name storage.",
    )

    @property
    def country(self) -> str | None:
        """Get country name with fallback logic."""
        # Try to get from country_code first (preferred)
        if self.country_code:
            country_name = self._get_country_name(self.country_code)
            if country_name:
                return country_name

        # Fall back to stored country name
        return self.country_name

    @staticmethod
    @lru_cache(maxsize=256)  # Cache country lookups for performance
    def _get_country_name(country_code: str | None) -> str | None:
        """Convert ISO 3166-1 alpha-2 code to country name."""
        if not country_code:
            return None

        try:
            country = pycountry.countries.get(alpha_2=country_code.upper())
            if country:
                return getattr(country, "name", None)
        except (AttributeError, KeyError):
            pass

        return None


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


class AuthorSearchResult(Author):
    """An author search result with relevance score."""

    score: float = Field(
        description="Relevance score (0-100) for search results",
        ge=0,
        le=100,
    )
