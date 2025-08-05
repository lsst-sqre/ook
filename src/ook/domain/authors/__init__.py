"""Author domain models and utilities."""

from __future__ import annotations

from ._countries import get_country_name, normalize_country_code
from ._models import Address, Affiliation, Author, AuthorSearchResult
from ._nameparser import NameFormat, NameParser, ParsedName

__all__ = [
    "Address",
    "Affiliation",
    "Author",
    "AuthorSearchResult",
    "NameFormat",
    "NameParser",
    "ParsedName",
    "get_country_name",
    "normalize_country_code",
]
