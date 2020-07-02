"""Tests for the ook.ingest.algolia.expiration module."""

import pytest

from ook.ingest.algolia.expiration import escape_facet_value


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://sqr-006.lsst.io/", '"https://sqr-006.lsst.io/"'),
        ("O'clock", r'"O\'clock"'),
        ('"cool"', r'"\"cool\""'),
    ],
)
def test_escape_facet_value(value: str, expected: str) -> None:
    assert escape_facet_value(value) == expected
