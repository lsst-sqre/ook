"""Tests for the migrate-country-codes CLI command."""

from __future__ import annotations

from ook.cli import migrate_country_codes
from ook.domain.authors import normalize_country_code


def test_country_name_conversion() -> None:
    """Test the country name to code conversion function used by CLI."""
    # Test cases that should work with normalize_country_code
    test_cases = [
        ("United States", "US"),
        ("Canada", "CA"),
        ("United Kingdom", "GB"),
        ("France", "FR"),
        ("Germany", "DE"),
        ("Japan", "JP"),
        ("USA", "US"),  # Custom mapping
        ("UK", "GB"),  # Custom mapping
    ]

    for country_name, expected_code in test_cases:
        result = normalize_country_code(country_name)
        assert result == expected_code, (
            f"Expected {country_name} -> {expected_code}, got {result}"
        )


def test_country_name_conversion_failures() -> None:
    """Test handling of invalid country names."""
    invalid_names = [
        "Invalid Country",
        "Not A Real Place",
        "Xyz",
        "",
        None,
    ]

    for invalid_name in invalid_names:
        result = normalize_country_code(invalid_name)
        assert result is None, (
            f"Expected None for {invalid_name}, got {result}"
        )


def test_cli_import() -> None:
    """Test that the CLI command can be imported successfully."""
    # Verify the function exists and is callable
    assert callable(migrate_country_codes)

    # Verify it's a Click command (has click attributes)
    assert hasattr(migrate_country_codes, "callback")
    assert hasattr(migrate_country_codes, "params")


def test_custom_country_mappings() -> None:
    """Test the custom country mappings used by the CLI."""
    # These are the custom mappings from the CLI implementation
    custom_mappings = [
        ("USA", "US"),
        ("UK", "GB"),
        ("The Netherlands", "NL"),
        ("People's Republic of China", "CN"),
        ("Czech Republic", "CZ"),
    ]

    for input_name, expected_code in custom_mappings:
        result = normalize_country_code(input_name)
        assert result == expected_code, (
            f"Custom mapping: {input_name} -> {expected_code}, got {result}"
        )


def test_fuzzy_matching_examples() -> None:
    """Test fuzzy matching examples that the CLI would handle."""
    fuzzy_cases = [
        ("United States of America", "US"),
        ("USA", "US"),
        ("Great Britain", "GB"),
        ("UK", "GB"),
    ]

    for input_name, expected_code in fuzzy_cases:
        result = normalize_country_code(input_name)
        assert result == expected_code, (
            f"Fuzzy match: {input_name} -> {expected_code}, got {result}"
        )


def test_edge_cases() -> None:
    """Test edge cases that the CLI migration would encounter."""
    edge_cases = [
        # Whitespace handling
        ("  USA  ", "US"),
        ("\tCanada\t", "CA"),
        # Case variations
        ("usa", "US"),
        ("Usa", "US"),
        ("USA", "US"),
        # Empty/null cases
        ("", None),
        (None, None),
    ]

    for input_value, expected_result in edge_cases:
        result = normalize_country_code(input_value)
        assert result == expected_result, (
            f"Edge case: {input_value!r} -> {expected_result}, got {result}"
        )


def test_bulk_processing_logic() -> None:
    """Test the logic for processing all records at once."""
    # Simulate all country names that might be processed in one operation
    all_country_names = [
        "United States",
        "Canada",
        "UK",
        "Invalid Country Name",
        "Germany",
        None,
        "",
        "France",
        "Australia",
        "Japan",
        "Non-existent Country",
    ]

    successful_conversions = 0
    failed_conversions = 0
    conversions = {}
    failed_names = []

    # Process all records like the CLI command does
    for i, country_name in enumerate(all_country_names):
        result = normalize_country_code(country_name)
        if result is not None:
            conversions[f"affiliation_{i}"] = {
                "country_name": country_name,
                "country_code": result,
            }
            successful_conversions += 1
        else:
            failed_names.append(country_name)
            failed_conversions += 1

    # Should successfully convert valid country names
    assert successful_conversions >= 7
    # Should fail on invalid inputs
    assert failed_conversions >= 3

    # Verify conversions contain expected mappings
    assert any(conv["country_code"] == "US" for conv in conversions.values())
    assert any(conv["country_code"] == "CA" for conv in conversions.values())
    assert any(conv["country_code"] == "GB" for conv in conversions.values())
