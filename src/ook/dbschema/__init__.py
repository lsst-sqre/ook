"""SQLAlchemy database schema."""

from .authors import SqlAffiliation, SqlAuthor, SqlAuthorAffiliation
from .base import Base
from .glossary import SqlTerm, term_relationships
from .linkcheck import (
    SqlCheckedUrl,
    SqlLinkCheck,
    SqlLinkCheckUrl,
    SqlUrlOccurrence,
)
from .links import SqlLink, SqlSdmColumnLink, SqlSdmSchemaLink, SqlSdmTableLink
from .resources import (
    SqlContributor,
    SqlDocumentResource,
    SqlExternalReference,
    SqlResource,
    SqlResourceRelation,
)
from .sdmschemas import SqlSdmColumn, SqlSdmSchema, SqlSdmTable

__all__ = [
    "Base",
    "SqlAffiliation",
    "SqlAuthor",
    "SqlAuthorAffiliation",
    "SqlCheckedUrl",
    "SqlContributor",
    "SqlDocumentResource",
    "SqlExternalReference",
    "SqlLink",
    "SqlLinkCheck",
    "SqlLinkCheckUrl",
    "SqlResource",
    "SqlResourceRelation",
    "SqlSdmColumn",
    "SqlSdmColumnLink",
    "SqlSdmSchema",
    "SqlSdmSchemaLink",
    "SqlSdmTable",
    "SqlSdmTableLink",
    "SqlTerm",
    "SqlUrlOccurrence",
    "term_relationships",
]
