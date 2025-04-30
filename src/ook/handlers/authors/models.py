"""API models."""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, Field, HttpUrl, model_validator

from ook.domain.authors import Author as AuthorDomain

__all__ = ["Author"]


class Affiliation(BaseModel):
    """An affiliation."""

    name: str = Field(description="Name of the affiliation.")

    internal_id: str = Field(
        description="Internal ID of the affiliation.",
    )

    address: str | None = Field(
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

    orcid: HttpUrl | None = Field(
        default=None,
        description="ORCID of the author (URL), or null if not available.",
    )

    notes: list[str] = Field(
        default_factory=list,
        description="Notes about the author.",
    )

    affiliations: list[Affiliation] = Field(
        default_factory=list,
        description="The author's affiliations.",
    )

    @model_validator(mode="before")
    @classmethod
    def validate_orcid(cls, data: Any) -> Any:
        """Convert ORCID identifier to a URL if necessary."""
        if (
            isinstance(data, dict)
            and "orcid" in data
            and data["orcid"] is not None
        ):
            data["orcid"] = cls._format_orcid_url(data["orcid"])
        elif hasattr(data, "orcid") and data.orcid is not None:
            data.orcid = cls._format_orcid_url(data.orcid)
        return data

    @staticmethod
    def _format_orcid_url(orcid: str) -> str:
        """Format ORCID identifier as a URL."""
        if not isinstance(orcid, HttpUrl) and not str(orcid).startswith(
            "http"
        ):
            # Format it as a URL by adding the orcid.org domain
            orcid_id = str(orcid).lstrip("/")
            return f"https://orcid.org/{orcid_id}"
        return orcid

    @classmethod
    def from_domain(cls, author: AuthorDomain) -> Self:
        """Create an AuthorResponse from a domain Author."""
        return cls.model_validate(author, from_attributes=True)
