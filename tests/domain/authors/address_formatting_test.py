"""Tests for address formatting functionality."""

from __future__ import annotations

from ook.domain.authors import Address


class TestAddressFormatting:
    """Test address formatting with google-i18n-address."""

    def test_address_formatting_us_complete(self) -> None:
        """Test US address formatting with complete address."""
        address = Address(
            street="950 Charter St",
            city="Redwood City",
            state="CA",
            postal_code="94063",
            country_code="US",
        )
        formatted = address.formatted
        assert formatted is not None
        assert "950 Charter St" in formatted
        # google-i18n-address may uppercase city names
        assert "REDWOOD CITY" in formatted or "Redwood City" in formatted
        assert "CA" in formatted
        assert "94063" in formatted

    def test_address_formatting_us_partial(self) -> None:
        """Test US address formatting with partial address."""
        address = Address(
            city="Boston",
            state="MA",
            country_code="US",
        )
        formatted = address.formatted
        assert formatted is not None
        assert "BOSTON" in formatted or "Boston" in formatted
        assert "MA" in formatted

    def test_address_formatting_uk(self) -> None:
        """Test UK address formatting."""
        address = Address(
            street="10 Downing Street",
            city="London",
            postal_code="SW1A 2AA",
            country_code="GB",
        )
        formatted = address.formatted
        assert formatted is not None
        assert "10 Downing Street" in formatted
        assert "LONDON" in formatted or "London" in formatted
        assert "SW1A 2AA" in formatted

    def test_address_formatting_france(self) -> None:
        """Test French address formatting."""
        address = Address(
            street="55 Rue du Faubourg Saint-Honoré",
            city="Paris",
            postal_code="75008",
            country_code="FR",
        )
        formatted = address.formatted
        assert formatted is not None
        assert "55 Rue du Faubourg Saint-Honoré" in formatted
        assert "PARIS" in formatted or "Paris" in formatted
        assert "75008" in formatted

    def test_address_formatting_minimal(self) -> None:
        """Test address formatting with minimal information."""
        address = Address(
            city="Boston",
            country_code="US",
        )
        formatted = address.formatted
        assert formatted is not None
        # Minimal address should still produce some output
        assert "BOSTON" in formatted or "Boston" in formatted

    def test_address_formatting_empty(self) -> None:
        """Test address formatting with empty address."""
        address = Address()
        assert address.formatted is None

    def test_address_formatting_fallback_country_name(self) -> None:
        """Test formatting with country name instead of code."""
        address = Address(
            street="1600 Pennsylvania Ave",
            city="Washington",
            state="DC",
            postal_code="20500",
            country_name="United States",
        )
        formatted = address.formatted
        assert formatted is not None
        assert "1600 Pennsylvania Ave" in formatted
        assert "Washington" in formatted

    def test_address_formatting_unicode_characters(self) -> None:
        """Test formatting with Unicode characters."""
        address = Address(
            street="北京市海淀区清华大学",
            city="北京",
            postal_code="100084",
            country_code="CN",
        )
        formatted = address.formatted
        assert formatted is not None
        assert "北京市海淀区清华大学" in formatted
        assert "北京" in formatted

    def test_address_formatting_invalid_country_code(self) -> None:
        """Test graceful handling of invalid country code."""
        address = Address(
            street="123 Main St",
            city="Nowhere",
            country_code="INVALID",
        )
        # Should not raise an exception, use fallback formatting
        formatted = address.formatted
        assert formatted is not None
        assert "123 Main St" in formatted
        assert "Nowhere" in formatted

    def test_address_formatting_fallback_method(self) -> None:
        """Test the fallback formatting method directly."""
        address = Address(
            street="123 Test Street",
            city="Test City",
            state="Test State",
            postal_code="12345",
            country_name="Test Country",
        )
        fallback = address._format_address_fallback()
        assert fallback is not None
        assert "123 Test Street" in fallback
        assert "Test City" in fallback
        assert "Test State 12345" in fallback
        assert "Test Country" in fallback

    def test_address_formatting_partial_state_postal(self) -> None:
        """Test fallback formatting with only state or postal code."""
        # Test with only state
        address_state_only = Address(
            street="123 Test St",
            city="Test City",
            state="CA",
            country_name="USA",
        )
        fallback = address_state_only._format_address_fallback()
        assert fallback is not None
        assert "CA" in fallback

        # Test with only postal code
        address_postal_only = Address(
            street="123 Test St",
            city="Test City",
            postal_code="12345",
            country_name="USA",
        )
        fallback = address_postal_only._format_address_fallback()
        assert fallback is not None
        assert "12345" in fallback

    def test_country_property_precedence(self) -> None:
        """Test that country property uses country_code first."""
        address = Address(
            country_code="US",
            country_name="Old Country Name",
        )
        # The country property should return the name from country_code
        assert address.country == "United States"

    def test_country_property_fallback(self) -> None:
        """Test that country property falls back to country_name."""
        address = Address(
            country_name="Some Custom Country",
        )
        # Should fall back to country_name
        assert address.country == "Some Custom Country"

    def test_country_property_none(self) -> None:
        """Test that country property returns None when both are missing."""
        address = Address()
        assert address.country is None
