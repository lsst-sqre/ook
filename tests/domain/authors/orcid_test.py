"""Tests for ORCID normalization in the authors domain."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from ook.domain.authors import Orcid, normalize_orcid

CANONICAL = "0000-0003-3001-676X"


@pytest.mark.parametrize(
    "value",
    [
        "0000-0003-3001-676X",
        "0000-0003-3001-676x",
        "https://orcid.org/0000-0003-3001-676X",
        "http://orcid.org/0000-0003-3001-676X",
        "https://www.orcid.org/0000-0003-3001-676X",
        "www.orcid.org/0000-0003-3001-676X",
        "orcid.org/0000-0003-3001-676X",
        "https://orcid.org/0000-0003-3001-676X/",
        "orcid.org/0000-0003-3001-676x/",
        "  https://orcid.org/0000-0003-3001-676X  ",
        "HTTPS://ORCID.ORG/0000-0003-3001-676X",
    ],
)
def test_normalize_orcid_accepts(value: str) -> None:
    assert normalize_orcid(value) == CANONICAL


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "Jonathan Sick",
        "https://example.com/0000-0003-3001-676X",
        "example.com/0000-0003-3001-676X",
        "https://orcid.org/",
        "000000033001676X",  # compact, hyphen-less form
        "0000-0003-3001-676",  # too short
        "0000-0003-3001-676XX",  # too long
        "0000-0003-3001-676Y",  # non-X final character
        "0000-0003-3001-6760",  # valid shape, wrong check digit
        "0000-0001-2345-6788",  # valid shape, wrong check digit
        # Only ASCII digits are ORCID digits: the fullwidth forms spell a
        # different string, which would defeat the equality lookup.
        "\uff10\uff10\uff10\uff10-\uff10\uff10\uff10\uff13-\uff13\uff10\uff10\uff11-\uff16\uff17\uff16X",
        "\uff10000-0003-3001-676X",
        # Only orcid.org is the ORCID host: a homoglyph host is a foreign one.
        "https://orc\u0131d.org/0000-0003-3001-676X",
        "https://ORC\u0130D.ORG/0000-0003-3001-676X",
    ],
)
def test_normalize_orcid_rejects(value: str) -> None:
    with pytest.raises(ValueError, match="ORCID"):
        normalize_orcid(value)


def test_normalize_orcid_accepts_numeric_check_digit() -> None:
    assert normalize_orcid("0000-0001-2345-6789") == "0000-0001-2345-6789"


def test_orcid_annotated_type_normalizes() -> None:
    adapter = TypeAdapter(Orcid)
    assert adapter.validate_python("orcid.org/0000-0003-3001-676x") == (
        CANONICAL
    )


def test_orcid_annotated_type_raises_validation_error() -> None:
    adapter = TypeAdapter(Orcid)
    with pytest.raises(ValidationError):
        adapter.validate_python("Jonathan Sick")
