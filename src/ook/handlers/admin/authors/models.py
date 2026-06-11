"""API models for the admin authors endpoints."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, Field

from ook.domain.authors import AuthorAlias as AuthorAliasDomain

__all__ = ["AuthorAlias", "AuthorAliasRequest"]


class AuthorAliasRequest(BaseModel):
    """A request to create an author internal ID alias."""

    alias: str = Field(
        description="The alias internal ID.",
        min_length=1,
        examples=["marshallpj"],
    )

    canonical: str = Field(
        description=(
            "Internal ID of the canonical (root) author this alias "
            "resolves to."
        ),
        min_length=1,
        examples=["marshallp"],
    )


class AuthorAlias(BaseModel):
    """An alias for an author's internal ID."""

    alias: str = Field(description="The alias internal ID.")

    canonical: str = Field(
        description=(
            "Internal ID of the canonical (root) author this alias "
            "resolves to."
        ),
    )

    @classmethod
    def from_domain(cls, alias: AuthorAliasDomain) -> Self:
        """Create an AuthorAlias from a domain AuthorAlias."""
        return cls(
            alias=alias.internal_id,
            canonical=alias.author_internal_id,
        )
