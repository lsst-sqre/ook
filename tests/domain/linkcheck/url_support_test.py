"""Tests for URL support classification in the link check domain."""

from __future__ import annotations

import pytest

from ook.domain.linkcheck import is_supported_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/page", True),
        ("http://example.com", True),
        ("mailto:someone@example.com", False),
        ("ftp://example.com/file.txt", False),
        ("javascript:void(0)", False),
        ("http://[invalid", False),
        ("not a url", False),
        ("https://", False),
        ("", False),
    ],
)
def test_is_supported_url(url: str, *, expected: bool) -> None:
    """Only well-formed http(s) URLs with a host are supported."""
    assert is_supported_url(url) is expected
