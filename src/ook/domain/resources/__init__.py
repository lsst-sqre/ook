"""Domain for bibliographic resources."""

from ._class import ResourceClass
from ._contributor import Contributor, ContributorRole
from ._document import Document
from ._externalref import (
    ExternalContributor,
    ExternalContributorAffiliation,
    ExternalReference,
)
from ._relation import RelationType
from ._resource import (
    ExternalRelation,
    RelatedExternalReference,
    RelatedResourceSummary,
    Resource,
    ResourceRelation,
    ResourceSummary,
)
from ._type import ResourceType

__all__ = [
    "Contributor",
    "ContributorRole",
    "Document",
    "ExternalContributor",
    "ExternalContributorAffiliation",
    "ExternalReference",
    "ExternalRelation",
    "RelatedExternalReference",
    "RelatedResourceSummary",
    "RelationType",
    "Resource",
    "ResourceClass",
    "ResourceRelation",
    "ResourceSummary",
    "ResourceType",
]
