"""Tests for the name parser functionality."""

from __future__ import annotations

import pytest

from ook.domain.authors._nameparser import NameFormat, NameParser, ParsedName


@pytest.fixture
def parser() -> NameParser:
    """Provide a NameParser instance for tests."""
    return NameParser()


def test_nameparser_empty_query(parser: NameParser) -> None:
    """Test that NameParser handles empty queries correctly."""
    result = parser.parse("")
    assert result.original == ""
    assert result.format == NameFormat.COMPLEX
    assert result.surname is None
    assert result.given_name is None


def test_nameparser_whitespace_only_query(parser: NameParser) -> None:
    """Test that NameParser handles whitespace-only queries correctly."""
    result = parser.parse("   ")
    assert result.original == ""
    assert result.format == NameFormat.COMPLEX


def test_nameparser_single_word(parser: NameParser) -> None:
    """Test that NameParser correctly parses single word queries."""
    result = parser.parse("Sick")
    assert result.original == "Sick"
    assert result.format == NameFormat.SINGLE_WORD
    assert result.surname == "Sick"
    assert result.given_name is None
    assert result.initials is None
    assert result.suffix is None


def test_nameparser_simple_first_last(parser: NameParser) -> None:
    """Test that NameParser correctly parses simple 'First Last' format."""
    result = parser.parse("Jonathan Sick")
    assert result.original == "Jonathan Sick"
    assert result.format == NameFormat.FIRST_LAST
    assert result.surname == "Sick"
    assert result.given_name == "Jonathan"
    assert result.initials is None
    assert result.suffix is None


def test_nameparser_first_middle_last(parser: NameParser) -> None:
    """Test that NameParser correctly parses 'First Middle Last' format."""
    result = parser.parse("John A Smith")
    assert result.original == "John A Smith"
    assert result.format == NameFormat.FIRST_LAST
    assert result.surname == "Smith"
    assert result.given_name == "John A"
    assert result.initials == ["A"]
    assert result.suffix is None


def test_nameparser_compound_surname(parser: NameParser) -> None:
    """Test that NameParser correctly parses compound surnames like
    'Plazas Malagón'.
    """
    result = parser.parse("Andrés Plazas Malagón")
    assert result.original == "Andrés Plazas Malagón"
    assert result.format == NameFormat.FIRST_LAST
    assert result.surname == "Plazas Malagón"
    assert result.given_name == "Andrés"
    assert result.initials is None


def test_nameparser_compound_surname_with_middle_initial(
    parser: NameParser,
) -> None:
    """Test that NameParser correctly parses compound surnames with
    middle initials.
    """
    result = parser.parse("Andrés A. Plazas Malagón")
    assert result.original == "Andrés A. Plazas Malagón"
    assert result.format == NameFormat.FIRST_LAST
    assert result.surname == "Plazas Malagón"
    assert result.given_name == "Andrés A."
    assert result.initials == ["A"]


def test_nameparser_last_comma_first(parser: NameParser) -> None:
    """Test that NameParser correctly parses 'Last, First' format."""
    result = parser.parse("Sick, Jonathan")
    assert result.original == "Sick, Jonathan"
    assert result.format == NameFormat.LAST_COMMA_FIRST
    assert result.surname == "Sick"
    assert result.given_name == "Jonathan"
    assert result.initials is None
    assert result.suffix is None


def test_nameparser_last_comma_first_with_middle(parser: NameParser) -> None:
    """Test that NameParser correctly parses 'Last, First Middle' format."""
    result = parser.parse("Smith, John Alexander")
    assert result.original == "Smith, John Alexander"
    assert result.format == NameFormat.LAST_COMMA_FIRST
    assert result.surname == "Smith"
    assert result.given_name == "John Alexander"
    assert result.initials is None


def test_nameparser_last_comma_first_with_middle_initial(
    parser: NameParser,
) -> None:
    """Test that NameParser correctly parses 'Last, First M.' format."""
    result = parser.parse("Smith, John A.")
    assert result.original == "Smith, John A."
    assert result.format == NameFormat.LAST_COMMA_FIRST
    assert result.surname == "Smith"
    assert result.given_name == "John A."
    assert result.initials == ["A"]


def test_nameparser_compound_surname_comma_format(parser: NameParser) -> None:
    """Test that NameParser correctly parses compound surnames in comma
    format.
    """
    result = parser.parse("Plazas Malagón, Andrés A.")
    assert result.original == "Plazas Malagón, Andrés A."
    assert result.format == NameFormat.LAST_COMMA_FIRST
    assert result.surname == "Plazas Malagón"
    assert result.given_name == "Andrés A."
    assert result.initials == ["A"]


def test_nameparser_last_comma_single_initial(parser: NameParser) -> None:
    """Test that NameParser correctly parses 'Last, I' format (single
    initial).
    """
    result = parser.parse("Sick, J")
    assert result.original == "Sick, J"
    assert result.format == NameFormat.LAST_COMMA_INITIAL
    assert result.surname == "Sick"
    assert result.given_name is None
    assert result.initials == ["J"]
    assert result.suffix is None


def test_nameparser_last_comma_single_initial_with_period(
    parser: NameParser,
) -> None:
    """Test that NameParser correctly parses 'Last, I.' format (single initial
    with period).
    """
    result = parser.parse("Sick, J.")
    assert result.original == "Sick, J."
    assert result.format == NameFormat.LAST_COMMA_INITIAL
    assert result.surname == "Sick"
    assert result.given_name is None
    assert result.initials == ["J"]


def test_nameparser_last_comma_multiple_initials(parser: NameParser) -> None:
    """Test that NameParser correctly parses 'Last, I. J.' format (multiple
    initials).
    """
    result = parser.parse("García, M. J.")
    assert result.original == "García, M. J."
    assert result.format == NameFormat.LAST_COMMA_INITIAL
    assert result.surname == "García"
    assert result.given_name is None
    assert result.initials == ["M", "J"]


def test_nameparser_multiple_initials_mixed_periods(
    parser: NameParser,
) -> None:
    """Test that NameParser correctly parses initials with mixed period
    usage.
    """
    result = parser.parse("Smith, J K.")
    assert result.original == "Smith, J K."
    assert result.format == NameFormat.LAST_COMMA_INITIAL
    assert result.surname == "Smith"
    assert result.initials == ["J", "K"]


def test_nameparser_suffix_junior(parser: NameParser) -> None:
    """Test that NameParser correctly parses names with 'Jr.' suffix."""
    result = parser.parse("Neilsen, Jr., Eric H.")
    assert result.original == "Neilsen, Jr., Eric H."
    assert result.format == NameFormat.LAST_COMMA_FIRST
    assert result.surname == "Neilsen"
    assert result.given_name == "Eric H."
    assert result.initials == ["H"]
    assert result.suffix == "Jr."


def test_nameparser_suffix_senior(parser: NameParser) -> None:
    """Test that NameParser correctly parses names with 'Sr.' suffix."""
    result = parser.parse("Smith, Sr., John")
    assert result.original == "Smith, Sr., John"
    assert result.format == NameFormat.LAST_COMMA_FIRST
    assert result.surname == "Smith"
    assert result.given_name == "John"
    assert result.suffix == "Sr."


def test_nameparser_suffix_roman_numerals(parser: NameParser) -> None:
    """Test that NameParser correctly parses names with roman numeral
    suffixes.
    """
    test_cases = [
        ("Smith III", "Smith", None, "III"),
        ("Johnson IV", "Johnson", None, "IV"),
        ("Brown II", "Brown", None, "II"),
    ]

    for query, expected_surname, expected_given, expected_suffix in test_cases:
        result = parser.parse(query)
        assert result.surname == expected_surname
        assert result.given_name == expected_given
        assert result.suffix == expected_suffix


def test_nameparser_suffix_space_format(parser: NameParser) -> None:
    """Test that NameParser correctly parses suffixes in space-separated
    format.
    """
    result = parser.parse("Eric H. Neilsen Jr.")
    assert result.original == "Eric H. Neilsen Jr."
    assert result.format == NameFormat.FIRST_LAST
    assert result.surname == "Neilsen"
    assert result.given_name == "Eric H."
    assert result.initials == ["H"]
    assert result.suffix == "Jr."


def test_nameparser_complex_format_multiple_commas(parser: NameParser) -> None:
    """Test that NameParser handles complex format with multiple commas where
    middle is not a suffix.
    """
    result = parser.parse("Smith, NotASuffix, John, Something")
    assert result.format == NameFormat.COMPLEX


def test_nameparser_suffix_middle_position_valid(parser: NameParser) -> None:
    """Test that NameParser correctly parses names with suffix in middle
    position of three comma-separated parts.
    """
    result = parser.parse("Smith, Jr., John")
    assert result.original == "Smith, Jr., John"
    assert result.format == NameFormat.LAST_COMMA_FIRST
    assert result.surname == "Smith"
    assert result.given_name == "John"
    assert result.suffix == "Jr."


def test_nameparser_case_insensitive_suffixes(parser: NameParser) -> None:
    """Test that NameParser suffix detection is case insensitive."""
    result = parser.parse("Smith jr.")
    assert result.suffix == "jr."
    assert result.surname == "Smith"


def test_nameparser_suffix_without_period(parser: NameParser) -> None:
    """Test that NameParser correctly parses suffixes without periods."""
    result = parser.parse("Smith Jr")
    assert result.suffix == "Jr"
    assert result.surname == "Smith"


def test_nameparser_middle_initial_extraction_from_space_format(
    parser: NameParser,
) -> None:
    """Test that NameParser correctly extracts middle initials in space
    format.
    """
    test_cases = [
        ("John A Smith", ["A"]),
        ("Mary J. K. Johnson", ["J", "K"]),
        ("Robert Smith", None),  # No initials
        ("A B C Defgh", ["A", "B", "C"]),  # Multiple initials
    ]

    for query, expected_initials in test_cases:
        result = parser.parse(query)
        assert result.initials == expected_initials


def test_nameparser_unicode_names(parser: NameParser) -> None:
    """Test that NameParser correctly handles Unicode characters in names."""
    result = parser.parse("García, José")
    assert result.surname == "García"
    assert result.given_name == "José"
    assert result.format == NameFormat.LAST_COMMA_FIRST


def test_nameparser_special_characters_in_names(parser: NameParser) -> None:
    """Test that NameParser correctly handles names with special characters."""
    test_cases = [
        "O'Connor, Sean",
        "van der Berg, Hans",
        "Al-Rahman, Omar",
        "D'Angelo, Maria",
    ]

    for query in test_cases:
        result = parser.parse(query)
        # Should not crash and should parse as comma format
        assert result.format == NameFormat.LAST_COMMA_FIRST


def test_nameparser_extra_whitespace_handling(parser: NameParser) -> None:
    """Test that NameParser correctly handles extra whitespace."""
    result = parser.parse("  Smith  ,   John   ")
    assert result.format == NameFormat.LAST_COMMA_FIRST
    assert result.surname == "Smith"
    assert result.given_name == "John"


def test_nameparser_mixed_case_handling(parser: NameParser) -> None:
    """Test that NameParser correctly handles mixed case names."""
    result = parser.parse("mCdONALD, jOHN")
    assert result.format == NameFormat.LAST_COMMA_FIRST
    assert result.surname == "mCdONALD"
    assert result.given_name == "jOHN"


def test_nameparser_very_long_names(parser: NameParser) -> None:
    """Test that NameParser correctly handles very long names."""
    long_surname = "Verylongcompoundsurnamewithmanyparts"
    long_given = "Verylonggivennamewithmanyparts"
    query = f"{long_surname}, {long_given}"

    result = parser.parse(query)
    assert result.format == NameFormat.LAST_COMMA_FIRST
    assert result.surname == long_surname
    assert result.given_name == long_given


def test_nameparser_single_letter_names(parser: NameParser) -> None:
    """Test that NameParser correctly handles single letter names (edge
    case).
    """
    result = parser.parse("X, Y")
    # Single letters in comma format might be treated as complex
    # depending on implementation - this tests current behavior
    assert result.surname == "X"
    # Y could be treated as given name or initial


def test_parsed_name_dataclass_creation() -> None:
    """Test basic ParsedName dataclass functionality."""
    parsed = ParsedName(
        original="Test Query",
        format=NameFormat.FIRST_LAST,
        surname="Test",
        given_name="Query",
    )

    assert parsed.original == "Test Query"
    assert parsed.format == NameFormat.FIRST_LAST
    assert parsed.surname == "Test"
    assert parsed.given_name == "Query"
    assert parsed.initials is None
    assert parsed.suffix is None


def test_parsed_name_dataclass_with_all_fields() -> None:
    """Test ParsedName dataclass with all fields populated."""
    parsed = ParsedName(
        original="Smith, Jr., John A.",
        format=NameFormat.LAST_COMMA_FIRST,
        surname="Smith",
        given_name="John A.",
        initials=["A"],
        suffix="Jr.",
    )

    assert parsed.initials == ["A"]
    assert parsed.suffix == "Jr."


def test_name_format_enum_values() -> None:
    """Test that all expected NameFormat enum values exist."""
    expected_formats = {
        "first_last",
        "last_comma_first",
        "last_comma_initial",
        "single_word",
        "complex",
    }

    actual_formats = {name_format.value for name_format in NameFormat}
    assert actual_formats == expected_formats


def test_name_format_enum_comparison() -> None:
    """Test that NameFormat enum comparison works correctly."""
    format1 = NameFormat.FIRST_LAST
    format2 = NameFormat.FIRST_LAST
    format3 = NameFormat.SINGLE_WORD

    assert format1 == format2
    assert format1 != format3
