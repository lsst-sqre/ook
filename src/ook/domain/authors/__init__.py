"""Author domain models and utilities."""

from __future__ import annotations

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
]
