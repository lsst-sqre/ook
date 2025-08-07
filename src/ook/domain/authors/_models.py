"""Domain model for authors and affiliations."""

from __future__ import annotations

from i18naddress import format_address
from pydantic import BaseModel, Field

from ._countries import get_country_name

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
            country_name = get_country_name(self.country_code)
            if country_name:
                return country_name

        # Fall back to stored country name
        return self.country_name

    @property
    def formatted(self) -> str | None:
        """Generate formatted address using international standards."""
        return self._format_address()

    def _format_address(self) -> str | None:
        """Format address components into a formatted address string."""
        if not any(
            [
                self.street,
                self.city,
                self.state,
                self.postal_code,
                self.country_code,
                self.country_name,
            ]
        ):
            return None

        # Build address dict for google-i18n-address
        address_dict = {}
        if self.street:
            address_dict["street_address"] = self.street
        if self.city:
            address_dict["city"] = self.city
        if self.state:
            address_dict["country_area"] = self.state
        if self.postal_code:
            address_dict["postal_code"] = self.postal_code
        if self.country_code:
            address_dict["country_code"] = self.country_code
        elif self.country_name:
            # Fallback to country name if code not available
            address_dict["country_code"] = self.country_name

        try:
            return format_address(address_dict)
        except Exception:
            # Fallback to simple concatenation if formatting fails
            return self._format_address_fallback()

    def _format_address_fallback(self) -> str | None:
        """Fallback formatting when google-i18n-address fails."""
        components = [
            self.street,
            self.city,
            f"{self.state} {self.postal_code}".strip()
            if self.state or self.postal_code
            else None,
            self.country,
        ]
        return "\n".join(c for c in components if c)


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
