"""Tests for the shared HTTP redirect policy."""

from __future__ import annotations

from ook.domain.redirects import (
    MAX_REDIRECTS,
    PERMANENT_REDIRECT_CODES,
    REDIRECT_CODES,
    is_permanent_chain,
)


def test_permanent_codes_are_followed_redirects() -> None:
    """Every permanent code is also a code the fetchers follow."""
    assert PERMANENT_REDIRECT_CODES <= REDIRECT_CODES


def test_hop_cap_is_positive() -> None:
    """The hop cap allows at least one redirect to be followed."""
    assert MAX_REDIRECTS > 0


def test_all_permanent_chain_is_permanent() -> None:
    """A chain of only 301 and 308 hops is permanent."""
    assert is_permanent_chain([301, 308, 301]) is True


def test_chain_with_a_temporary_hop_is_not_permanent() -> None:
    """A single temporary hop makes the whole chain temporary."""
    assert is_permanent_chain([301, 302, 308]) is False


def test_empty_chain_is_permanent() -> None:
    """An empty chain is vacuously permanent.

    Callers distinguish "did not redirect" before asking, so the vacuous
    answer is never the one they report.
    """
    assert is_permanent_chain([]) is True
