from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ..authors import Author, Collaboration
from ..base32id import Base32Id

__all__ = [
    "BaseContributor",
    "CollaborationContributor",
    "Contributor",
    "ContributorRole",
    "ContributorType",
    "IndividualContributor",
]


class ContributorRole(StrEnum):
    """Enumeration of author roles, based on DataCite roles.

    https://datacite-metadata-schema.readthedocs.io/en/4.6/properties/contributor/#a-contributortype
    """

    creator = "Creator"
    """A person or organization primarily responsible for the creation of the
    resource (e.g., the authors).

    This is *not* a DataCite contributor role, but instead maps a contributor
    as a creator in the DataCite schema to avoid having a separate
    `creator` field in the resource and association database table.
    """

    contact_person = "ContactPerson"
    data_collector = "DataCollector"
    data_curator = "DataCurator"
    data_manager = "DataManager"
    distributor = "Distributor"
    editor = "Editor"
    hosting_institution = "HostingInstitution"
    producer = "Producer"
    project_leader = "ProjectLeader"
    project_manager = "ProjectManager"
    project_member = "ProjectMember"
    registration_agency = "RegistrationAgency"
    registration_authority = "RegistrationAuthority"
    related_person = "RelatedPerson"
    researcher = "Researcher"
    research_group = "ResearchGroup"
    rights_holder = "RightsHolder"
    sponsor = "Sponsor"
    supervisor = "Supervisor"
    translator = "Translator"
    work_package_leader = "WorkPackageLeader"
    other = "Other"


class ContributorType(StrEnum):
    """Type of contributor."""

    individual = "individual"
    collaboration = "collaboration"


class BaseContributor(BaseModel):
    """Base class for all contributors."""

    # TODO(jonathansick): is this neceessary?
    resource_id: Annotated[Base32Id, Field(description="Resource identifier.")]

    order: Annotated[
        int,
        Field(
            description=(
                "Order of the contributor in the list for a given role."
            )
        ),
    ]

    role: Annotated[
        ContributorRole,
        Field(
            description="Role of the author in the resource.",
            examples=["Creator", "Editor"],
        ),
    ]


class IndividualContributor(BaseContributor):
    """A contributor who is an individual author."""

    type: Literal[ContributorType.individual] = ContributorType.individual

    author: Annotated[
        Author,
        Field(description="Author details for the individual contributor."),
    ]


class CollaborationContributor(BaseContributor):
    """A contributor who is a collaboration."""

    type: Literal[ContributorType.collaboration] = (
        ContributorType.collaboration
    )

    collaboration: Annotated[
        Collaboration,
        Field(description="Collaboration details for the contributor."),
    ]


# Union type with discriminator
Contributor = Annotated[
    IndividualContributor | CollaborationContributor,
    Field(discriminator="type"),
]
"""Contributor field type, which can be either an individual or a
collaboration.
"""
