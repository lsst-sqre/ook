"""SQLAlchemy database schema."""

from .authors import SqlAffiliation, SqlAuthor, SqlAuthorAffiliation
from .base import Base
from .glossary import SqlTerm, term_relationships
from .links import SqlLink, SqlSdmColumnLink, SqlSdmSchemaLink, SqlSdmTableLink
from .sdmschemas import SqlSdmColumn, SqlSdmSchema, SqlSdmTable

__all__ = [
    "Base",
    "SqlAffiliation",
    "SqlAuthor",
    "SqlAuthorAffiliation",
    "SqlLink",
    "SqlSdmColumn",
    "SqlSdmColumnLink",
    "SqlSdmSchema",
    "SqlSdmSchemaLink",
    "SqlSdmTable",
    "SqlSdmTableLink",
    "SqlTerm",
    "term_relationships",
]
