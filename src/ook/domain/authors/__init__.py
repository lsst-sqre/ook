"""Author domain models and utilities."""

from __future__ import annotations

from ._countries import get_country_name, normalize_country_code
from ._models import (
    Address,
    Affiliation,
    Author,
    AuthorAlias,
    AuthorSearchResult,
)
from ._nameparser import NameFormat, NameParser, ParsedName
from ._orcid import Orcid, normalize_orcid

__all__ = [
    "Address",
    "Affiliation",
    "Author",
    "AuthorAlias",
    "AuthorSearchResult",
    "NameFormat",
    "NameParser",
    "Orcid",
    "ParsedName",
    "get_country_name",
    "normalize_country_code",
    "normalize_orcid",
]
