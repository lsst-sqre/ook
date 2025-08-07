"""Tests for Address domain model country code functionality."""

from __future__ import annotations

from ook.domain.authors import Address


def test_country_fallback_with_country_code() -> None:
    """Test fallback logic when country_code is available."""
    address = Address(country_code="US", country_name="Old Country Name")

    # Should use country name derived from country_code
    assert address.country == "United States"


def test_country_fallback_without_country_code() -> None:
    """Test fallback logic when country_code is not available."""
    address = Address(country_code=None, country_name="United States")

    # Should fallback to stored country_name
    assert address.country == "United States"


def test_country_fallback_with_invalid_country_code() -> None:
    """Test fallback logic when country_code is invalid."""
    address = Address(country_code="XX", country_name="Fallback Country")

    # Should fallback to stored country_name when code is invalid
    assert address.country == "Fallback Country"


def test_country_fallback_both_none() -> None:
    """Test fallback logic when both fields are None."""
    address = Address(country_code=None, country_name=None)

    # Should return None when both are unavailable
    assert address.country is None


def test_country_fallback_empty_strings() -> None:
    """Test fallback logic with empty strings."""
    address = Address(country_code="", country_name="Some Country")

    # Should fallback to country_name when country_code is empty
    assert address.country == "Some Country"


def test_country_code_to_name_conversion() -> None:
    """Test ISO code to country name conversion for common codes."""
    test_cases = [
        ("US", "United States"),
        ("CA", "Canada"),
        ("GB", "United Kingdom"),
        ("FR", "France"),
        ("DE", "Germany"),
        ("JP", "Japan"),
    ]

    for country_code, expected_name in test_cases:
        address = Address(country_code=country_code)
        assert address.country == expected_name


def test_country_code_case_insensitive() -> None:
    """Test that country codes work regardless of case."""
    address_lower = Address(country_code="us")
    address_upper = Address(country_code="US")

    assert address_lower.country == "United States"
    assert address_upper.country == "United States"


def test_address_with_all_fields() -> None:
    """Test Address with all fields populated."""
    address = Address(
        street="123 Main St",
        city="Anytown",
        state="CA",
        postal_code="12345",
        country_code="US",
        country_name="Legacy Name",
    )

    assert address.street == "123 Main St"
    assert address.city == "Anytown"
    assert address.state == "CA"
    assert address.postal_code == "12345"
    assert address.country_code == "US"
    assert address.country_name == "Legacy Name"
    assert (
        address.country == "United States"
    )  # Uses country_code, not country_name


def test_address_serialization() -> None:
    """Test that Address can be serialized and deserialized."""
    original = Address(
        street="456 Oak Ave",
        city="Test City",
        state="NY",
        postal_code="67890",
        country_code="CA",
        country_name="Old Canada Name",
    )

    # Serialize to dict
    data = original.model_dump()

    # Deserialize from dict
    restored = Address.model_validate(data)

    assert restored.street == original.street
    assert restored.city == original.city
    assert restored.state == original.state
    assert restored.postal_code == original.postal_code
    assert restored.country_code == original.country_code
    assert restored.country_name == original.country_name
    assert restored.country == "Canada"  # Should use country_code logic
