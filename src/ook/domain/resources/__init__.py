"""Domain for bibliographic resources."""

from ._class import ResourceClass
from ._contributor import (
    BaseContributor,
    CollaborationContributor,
    Contributor,
    ContributorRole,
    ContributorType,
    IndividualContributor,
)
from ._document import Document
from ._externalref import (
    ExternalContributor,
    ExternalContributorAffiliation,
    ExternalReference,
)
from ._relation import RelationType
from ._resource import ExternalRelation, Resource, ResourceRelation
from ._type import ResourceType

__all__ = [
    "BaseContributor",
    "CollaborationContributor",
    "Contributor",
    "ContributorRole",
    "ContributorType",
    "Document",
    "ExternalContributor",
    "ExternalContributorAffiliation",
    "ExternalReference",
    "ExternalRelation",
    "IndividualContributor",
    "RelationType",
    "Resource",
    "ResourceClass",
    "ResourceRelation",
    "ResourceType",
]
