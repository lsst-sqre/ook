"""Test country normalization utilities."""

from __future__ import annotations

from ook.domain.authors import get_country_name, normalize_country_code


def test_normalize_country_code_standard_cases() -> None:
    """Test normalization of common country variations."""
    # Test custom mappings
    assert normalize_country_code("USA") == "US"
    assert normalize_country_code("UK") == "GB"
    assert normalize_country_code("The Netherlands") == "NL"

    # Test already valid ISO codes
    assert normalize_country_code("US") == "US"
    assert normalize_country_code("GB") == "GB"
    assert normalize_country_code("CA") == "CA"


def test_normalize_country_code_fuzzy_search() -> None:
    """Test fuzzy search functionality."""
    # Test full country names that need fuzzy matching
    assert normalize_country_code("United States") == "US"
    assert normalize_country_code("Canada") == "CA"
    assert normalize_country_code("Germany") == "DE"


def test_normalize_country_code_edge_cases() -> None:
    """Test edge cases for country code normalization."""
    # Test None and empty strings
    assert normalize_country_code(None) is None
    assert normalize_country_code("") is None

    # Test invalid country codes
    assert normalize_country_code("INVALID") is None
    assert normalize_country_code("XYZ") is None


def test_get_country_name_standard_cases() -> None:
    """Test getting country names from ISO codes."""
    assert get_country_name("US") == "United States"
    assert get_country_name("GB") == "United Kingdom"
    assert get_country_name("CA") == "Canada"
    assert get_country_name("DE") == "Germany"


def test_get_country_name_edge_cases() -> None:
    """Test edge cases for country name lookup."""
    # Test None and empty strings
    assert get_country_name(None) is None
    assert get_country_name("") is None

    # Test invalid country codes
    assert get_country_name("XY") is None
    assert get_country_name("INVALID") is None


def test_case_insensitive_lookup() -> None:
    """Test that country code lookup is case insensitive."""
    assert get_country_name("us") == "United States"
    assert get_country_name("Us") == "United States"
    assert get_country_name("uS") == "United States"
