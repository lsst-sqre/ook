"""Domain model for an external resource that was referenced."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ook.domain.resources._contributor import ContributorRole

from ._type import ResourceType

__all__ = [
    "ExternalContributor",
    "ExternalContributorAffiliation",
    "ExternalReference",
]


class ExternalReference(BaseModel):
    """An external resourced that is referenced by an Ook resource.

    This domain model roughly corresponds to DataCite's RelatedItem and
    RelatedIdentifier models:

    https://datacite-metadata-schema.readthedocs.io/en/4.6/properties/relateditem/#
    https://datacite-metadata-schema.readthedocs.io/en/4.6/properties/relatedidentifier/

    If a related identifier is known, then it can be used as a
    relatedIdentifier for the parent resource in the DataCite schema.
    Otherwise, this model can be used to store metadata about the external
    resource to better describe the reference.
    """

    url: Annotated[
        str | None, Field(description="URL of the external resource.")
    ] = None

    doi: Annotated[
        str | None,
        Field(
            description=(
                "Digital Object Identifier (DOI) of the external resource."
            )
        ),
    ] = None

    arxiv_id: Annotated[
        str | None,
        Field(
            description=(
                "arXiv identifier of the external resource, if applicable."
            )
        ),
    ] = None

    isbn: Annotated[
        str | None,
        Field(
            description=(
                "International Standard Book Number (ISBN) of the external "
                "resource, if applicable."
            )
        ),
    ] = None

    issn: Annotated[
        str | None,
        Field(
            description=(
                "International Standard Serial Number (ISSN) of the external "
                "resource, if applicable."
            )
        ),
    ] = None

    ads_bibcode: Annotated[
        str | None,
        Field(
            description=(
                "Astrophysics Data System (ADS) Bibcode of the external "
                "resource, if applicable."
            )
        ),
    ] = None

    type: Annotated[
        ResourceType | None,
        Field(
            description=(
                "Type of the external resource, if known. This can be used to "
                "indicate the nature of the resource (e.g., article, book, "
                "report)."
            )
        ),
    ] = None

    title: Annotated[
        str | None,
        Field(description=("Title of the external resource.")),
    ] = None

    publication_year: Annotated[
        str | None, Field(description="Year of publication.")
    ] = None

    volume: Annotated[
        str | None, Field(description="Volume of the external resource.")
    ] = None

    issue: Annotated[
        str | None, Field(description="Issue of the external resource.")
    ] = None

    number: Annotated[
        str | None, Field(description="Number of the external resource.")
    ] = None

    number_type: Annotated[
        Literal["Article", "Chapter", "Report", "Other"] | None,
        Field(
            description=(
                "Type of number field (DataCite vocabulary numberType)."
            )
        ),
    ]

    first_page: Annotated[
        str | None,
        Field(
            description=(
                "First page of the external resource within the related item."
            )
        ),
    ] = None

    last_page: Annotated[
        str | None,
        Field(
            description=(
                "Last page of the external resource within the related item."
            )
        ),
    ] = None

    publisher: Annotated[
        str | None,
        Field(description="Publisher of the external resource."),
    ] = None

    edition: Annotated[
        str | None,
        Field(description="Edition of the external resource, if applicable."),
    ] = None

    contributors: Annotated[
        list[ExternalContributor] | None,
        Field(
            description=(
                "List of contributors to the external resource, such as "
                "authors, editors, or other roles."
            ),
        ),
    ] = None


class ExternalContributor(BaseModel):
    """An external contributor that is referenced by an Ook resource."""

    name: Annotated[
        str, Field(description="Name of the external contributor.")
    ]

    type: Annotated[
        Literal["Personal", "Organizational"] | None,
        Field(
            description=(
                "Type of the external contributor (DataCite vocabulary "
                "nameType)."
            )
        ),
    ] = None

    orcid: Annotated[
        str | None, Field(description="ORCID URL (for people)")
    ] = None

    ror: Annotated[
        str | None, Field(description="ROR URL (for organizations)")
    ] = None

    given_name: Annotated[
        str | None,
        Field(
            description=(
                "Given name of the external contributor, if applicable."
            )
        ),
    ] = None

    surname: Annotated[
        str | None,
        Field(
            description=("Surname of the external contributor, if applicable.")
        ),
    ] = None

    affiliations: Annotated[
        list[ExternalContributorAffiliation] | None,
        Field(description="Affiliation of the external contributor."),
    ] = None

    role: Annotated[
        ContributorRole | None,
        Field(
            description=(
                "Role of the external contributor (e.g., author, editor)."
            )
        ),
    ] = None


class ExternalContributorAffiliation(BaseModel):
    """An external contributor's affiliation that is referenced by an Ook
    resource.
    """

    name: Annotated[
        str,
        Field(description="Name of the external contributor's affiliation."),
    ]

    ror: Annotated[
        str | None, Field(description="ROR URL of the instutution, if known.")
    ] = None
