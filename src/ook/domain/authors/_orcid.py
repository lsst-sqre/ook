"""Normalization and validation of ORCID identifiers."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BeforeValidator

__all__ = ["Orcid", "normalize_orcid"]

_ORCID_URL_PREFIX_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.)?orcid\.org/", re.IGNORECASE
)
"""The URL forms an ORCID may be spelled in, ahead of the identifier itself.

Only ``orcid.org`` is accepted as the host: an identifier-shaped path segment
on any other host is not an ORCID, and reducing such a URL to its last path
segment would answer a question the client did not ask.
"""

_ORCID_PATTERN = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[0-9X]")
"""The shape of a bare ORCID identifier, once uppercased."""


def normalize_orcid(value: str) -> str:
    r"""Normalize an ORCID in any of its spellings to the bare identifier.

    Accepts the bare identifier (``0000-0003-3001-676X``), a lowercase
    checksum character, and the ``orcid.org`` URL forms — with or without an
    ``https://``/``http://`` scheme, a ``www.`` prefix, or a trailing slash —
    with surrounding whitespace ignored.

    Parameters
    ----------
    value
        The ORCID as written by the client.

    Returns
    -------
    str
        The bare, uppercase ORCID identifier (``0000-0003-3001-676X``), in
        the form stored in the database.

    Raises
    ------
    ValueError
        Raised if the value is not an ORCID: a URL on a host other than
        ``orcid.org``, anything that does not match
        ``\d{4}-\d{4}-\d{4}-\d{3}[0-9X]`` once normalized (including the
        hyphen-less 16-character compact form), or a well-formed identifier
        whose ISO 7064 mod-11-2 check digit does not verify.
    """
    candidate = value.strip()
    url_prefix = _ORCID_URL_PREFIX_PATTERN.match(candidate)
    if url_prefix:
        candidate = candidate[url_prefix.end() :]
    candidate = candidate.rstrip("/").upper()

    if not _ORCID_PATTERN.fullmatch(candidate):
        raise ValueError(
            f"{value!r} is not an ORCID identifier; expected the form "
            "0000-0003-3001-676X or an orcid.org URL for it"
        )
    if candidate[-1] != _compute_check_digit(candidate):
        raise ValueError(
            f"{value!r} is not a valid ORCID identifier: its check digit "
            "does not verify"
        )
    return candidate


def _compute_check_digit(orcid: str) -> str:
    """Compute the ISO 7064 mod-11-2 check digit for a shape-checked ORCID.

    Parameters
    ----------
    orcid
        A bare, uppercase ORCID identifier. Only its leading 15 digits are
        read; the trailing check character is ignored.

    Returns
    -------
    str
        The check character the identifier's digits imply: ``0``-``9`` or
        ``X``.
    """
    digits = orcid.replace("-", "")
    total = 0
    for digit in digits[:-1]:
        total = (total + int(digit)) * 2
    remainder = (12 - total % 11) % 11
    return "X" if remainder == 10 else str(remainder)


Orcid = Annotated[str, BeforeValidator(normalize_orcid)]
"""An ORCID identifier, normalized from any of its accepted spellings."""
