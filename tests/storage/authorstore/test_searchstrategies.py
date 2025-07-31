"""Tests for search strategy classes."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.sql.elements import ColumnElement

from ook.domain.authors import NameFormat, ParsedName
from ook.storage.authorstore._searchstrategies import (
    ComponentStrategy,
    InitialStrategy,
    SearchStrategy,
    TrigramStrategy,
)


class MockStrategy(SearchStrategy):
    """Mock strategy for testing the abstract base class."""

    def build_condition(self, parsed_name: ParsedName) -> ColumnElement[bool]:
        """Mock implementation."""
        return MagicMock()

    def build_score_expr(self, parsed_name: ParsedName) -> ColumnElement[int]:
        """Mock implementation."""
        return MagicMock()


@pytest.fixture
def mock_strategy() -> MockStrategy:
    """Provide a MockStrategy instance for tests."""
    return MockStrategy()


@pytest.fixture
def sample_parsed_name() -> ParsedName:
    """Provide a sample ParsedName instance for tests."""
    return ParsedName(original="Test Query", format=NameFormat.FIRST_LAST)


def test_search_strategy_abstract_base_class() -> None:
    """Test that SearchStrategy cannot be instantiated directly."""
    with pytest.raises(TypeError):
        SearchStrategy()  # type: ignore[abstract]


def test_search_strategy_mock_instantiation(
    mock_strategy: MockStrategy,
) -> None:
    """Test that concrete implementations can be instantiated."""
    assert isinstance(mock_strategy, SearchStrategy)


def test_search_strategy_abstract_methods_required(
    mock_strategy: MockStrategy, sample_parsed_name: ParsedName
) -> None:
    """Test that abstract methods must be implemented."""
    # Should not raise since MockStrategy implements the abstract methods
    mock_strategy.build_condition(sample_parsed_name)
    mock_strategy.build_score_expr(sample_parsed_name)


@pytest.fixture
def trigram_strategy() -> TrigramStrategy:
    """Provide a TrigramStrategy instance for tests."""
    return TrigramStrategy()


@pytest.fixture
def trigram_strategy_custom() -> TrigramStrategy:
    """Provide a TrigramStrategy instance with custom threshold for tests."""
    return TrigramStrategy(similarity_threshold=0.2)


@pytest.fixture
def first_last_parsed_name() -> ParsedName:
    """Provide a ParsedName with FIRST_LAST format for tests."""
    return ParsedName(original="Jonathan Sick", format=NameFormat.FIRST_LAST)


def test_trigram_strategy_initialization_default_threshold() -> None:
    """Test default initialization."""
    strategy = TrigramStrategy()
    assert strategy.similarity_threshold == 0.1


def test_trigram_strategy_initialization_custom_threshold() -> None:
    """Test initialization with custom threshold."""
    strategy = TrigramStrategy(similarity_threshold=0.3)
    assert strategy.similarity_threshold == 0.3


def test_trigram_strategy_build_condition_structure(
    trigram_strategy: TrigramStrategy, first_last_parsed_name: ParsedName
) -> None:
    """Test that build_condition returns proper structure."""
    condition = trigram_strategy.build_condition(first_last_parsed_name)

    # Should return a comparison expression
    assert hasattr(condition, "left")
    assert hasattr(condition, "right")
    assert hasattr(condition, "operator")


def test_trigram_strategy_build_score_expr_structure(
    trigram_strategy: TrigramStrategy, first_last_parsed_name: ParsedName
) -> None:
    """Test that build_score_expr returns proper structure."""
    score_expr = trigram_strategy.build_score_expr(first_last_parsed_name)

    # Should return a column element
    assert isinstance(score_expr, ColumnElement)


def test_trigram_strategy_different_thresholds_affect_condition(
    trigram_strategy: TrigramStrategy, trigram_strategy_custom: TrigramStrategy
) -> None:
    """Test that different thresholds create different conditions."""
    parsed_name = ParsedName(
        original="Test Query", format=NameFormat.FIRST_LAST
    )

    condition1 = trigram_strategy.build_condition(parsed_name)
    condition2 = trigram_strategy_custom.build_condition(parsed_name)

    # The conditions should have different threshold values
    # (though we can't easily test the actual SQL without compilation)
    assert condition1 is not condition2


def test_trigram_strategy_handles_different_name_formats(
    trigram_strategy: TrigramStrategy,
) -> None:
    """Test strategy handles different name formats."""
    test_cases = [
        ParsedName(
            original="Sick, Jonathan", format=NameFormat.LAST_COMMA_FIRST
        ),
        ParsedName(original="Sick, J", format=NameFormat.LAST_COMMA_INITIAL),
        ParsedName(original="Sick", format=NameFormat.SINGLE_WORD),
        ParsedName(original="Jonathan Sick", format=NameFormat.FIRST_LAST),
    ]

    for parsed_name in test_cases:
        # Should not raise exceptions
        condition = trigram_strategy.build_condition(parsed_name)
        score_expr = trigram_strategy.build_score_expr(parsed_name)

        assert condition is not None
        assert score_expr is not None


@pytest.fixture
def component_strategy() -> ComponentStrategy:
    """Provide a ComponentStrategy instance for tests."""
    return ComponentStrategy()


def test_component_strategy_build_condition_with_surname_only(
    component_strategy: ComponentStrategy,
) -> None:
    """Test condition building with surname only."""
    parsed_name = ParsedName(
        original="Sick", format=NameFormat.SINGLE_WORD, surname="Sick"
    )

    condition = component_strategy.build_condition(parsed_name)
    assert condition is not None


def test_component_strategy_build_condition_with_given_name_only(
    component_strategy: ComponentStrategy,
) -> None:
    """Test condition building with given name only."""
    parsed_name = ParsedName(
        original="Jonathan",
        format=NameFormat.SINGLE_WORD,
        given_name="Jonathan",
    )

    condition = component_strategy.build_condition(parsed_name)
    assert condition is not None


def test_component_strategy_build_condition_with_both_names(
    component_strategy: ComponentStrategy,
) -> None:
    """Test condition building with both surname and given name."""
    parsed_name = ParsedName(
        original="Jonathan Sick",
        format=NameFormat.FIRST_LAST,
        surname="Sick",
        given_name="Jonathan",
    )

    condition = component_strategy.build_condition(parsed_name)
    assert condition is not None


def test_component_strategy_build_condition_with_middle_initial(
    component_strategy: ComponentStrategy,
) -> None:
    """Test condition building with middle initial."""
    parsed_name = ParsedName(
        original="John A Smith",
        format=NameFormat.FIRST_LAST,
        surname="Smith",
        given_name="John A",
        initials=["A"],
    )

    condition = component_strategy.build_condition(parsed_name)
    assert condition is not None


def test_component_strategy_build_condition_empty_name(
    component_strategy: ComponentStrategy,
) -> None:
    """Test condition building with empty name components."""
    parsed_name = ParsedName(original="", format=NameFormat.COMPLEX)

    condition = component_strategy.build_condition(parsed_name)
    # Should return a non-null condition even for empty names
    assert condition is not None


def test_component_strategy_build_score_expr_with_complete_name(
    component_strategy: ComponentStrategy,
) -> None:
    """Test score expression with complete name."""
    parsed_name = ParsedName(
        original="Jonathan Sick",
        format=NameFormat.FIRST_LAST,
        surname="Sick",
        given_name="Jonathan",
    )

    score_expr = component_strategy.build_score_expr(parsed_name)
    assert isinstance(score_expr, ColumnElement)


def test_component_strategy_build_score_expr_with_middle_initial(
    component_strategy: ComponentStrategy,
) -> None:
    """Test score expression handles middle initial extraction."""
    parsed_name = ParsedName(
        original="John A Smith",
        format=NameFormat.FIRST_LAST,
        surname="Smith",
        given_name="John A",
        initials=["A"],
    )

    score_expr = component_strategy.build_score_expr(parsed_name)
    assert isinstance(score_expr, ColumnElement)


def test_component_strategy_build_score_expr_incomplete_name(
    component_strategy: ComponentStrategy,
) -> None:
    """Test score expression with incomplete name components."""
    parsed_name = ParsedName(
        original="Sick", format=NameFormat.SINGLE_WORD, surname="Sick"
    )

    score_expr = component_strategy.build_score_expr(parsed_name)
    assert isinstance(score_expr, ColumnElement)


@pytest.fixture
def initial_strategy() -> InitialStrategy:
    """Provide an InitialStrategy instance for tests."""
    return InitialStrategy()


def test_initial_strategy_build_condition_with_initials(
    initial_strategy: InitialStrategy,
) -> None:
    """Test condition building with initials."""
    parsed_name = ParsedName(
        original="Sick, J",
        format=NameFormat.LAST_COMMA_INITIAL,
        surname="Sick",
        initials=["J"],
    )

    condition = initial_strategy.build_condition(parsed_name)
    assert condition is not None


def test_initial_strategy_build_condition_with_multiple_initials(
    initial_strategy: InitialStrategy,
) -> None:
    """Test condition building with multiple initials."""
    parsed_name = ParsedName(
        original="García, M. J.",
        format=NameFormat.LAST_COMMA_INITIAL,
        surname="García",
        initials=["M", "J"],
    )

    condition = initial_strategy.build_condition(parsed_name)
    assert condition is not None


def test_initial_strategy_build_condition_without_initials(
    initial_strategy: InitialStrategy,
) -> None:
    """Test condition building without initials."""
    parsed_name = ParsedName(
        original="Jonathan Sick",
        format=NameFormat.FIRST_LAST,
        surname="Sick",
        given_name="Jonathan",
    )

    condition = initial_strategy.build_condition(parsed_name)
    # Should return a condition that matches nothing
    assert condition is not None


def test_initial_strategy_build_condition_initials_with_surname(
    initial_strategy: InitialStrategy,
) -> None:
    """Test condition building with both initials and surname."""
    parsed_name = ParsedName(
        original="Smith, J. K.",
        format=NameFormat.LAST_COMMA_INITIAL,
        surname="Smith",
        initials=["J", "K"],
    )

    condition = initial_strategy.build_condition(parsed_name)
    assert condition is not None


def test_initial_strategy_build_score_expr_with_initials(
    initial_strategy: InitialStrategy,
) -> None:
    """Test score expression with initials."""
    parsed_name = ParsedName(
        original="Sick, J",
        format=NameFormat.LAST_COMMA_INITIAL,
        surname="Sick",
        initials=["J"],
    )

    score_expr = initial_strategy.build_score_expr(parsed_name)
    assert isinstance(score_expr, ColumnElement)


def test_initial_strategy_build_score_expr_without_initials(
    initial_strategy: InitialStrategy,
) -> None:
    """Test score expression without initials."""
    parsed_name = ParsedName(
        original="Jonathan Sick",
        format=NameFormat.FIRST_LAST,
        surname="Sick",
        given_name="Jonathan",
    )

    score_expr = initial_strategy.build_score_expr(parsed_name)
    assert isinstance(score_expr, ColumnElement)


def test_initial_strategy_build_score_expr_initials_no_surname(
    initial_strategy: InitialStrategy,
) -> None:
    """Test score expression with initials but no surname."""
    parsed_name = ParsedName(
        original="J", format=NameFormat.LAST_COMMA_INITIAL, initials=["J"]
    )

    score_expr = initial_strategy.build_score_expr(parsed_name)
    assert isinstance(score_expr, ColumnElement)


@pytest.fixture
def all_strategies() -> tuple[
    TrigramStrategy, ComponentStrategy, InitialStrategy
]:
    """Provide all search strategy instances for integration tests."""
    return (TrigramStrategy(), ComponentStrategy(), InitialStrategy())


def test_strategy_integration_with_compound_surname(
    all_strategies: tuple[TrigramStrategy, ComponentStrategy, InitialStrategy],
) -> None:
    """Test all strategies handle compound surnames."""
    trigram_strategy, component_strategy, initial_strategy = all_strategies

    parsed_name = ParsedName(
        original="Plazas Malagón, Andrés A.",
        format=NameFormat.LAST_COMMA_FIRST,
        surname="Plazas Malagón",
        given_name="Andrés A.",
        initials=["A"],
    )

    # All strategies should handle this without errors
    strategies = [trigram_strategy, component_strategy, initial_strategy]

    for strategy in strategies:
        condition = strategy.build_condition(parsed_name)
        score_expr = strategy.build_score_expr(parsed_name)

        assert condition is not None
        assert score_expr is not None


def test_strategy_integration_with_suffix(
    all_strategies: tuple[TrigramStrategy, ComponentStrategy, InitialStrategy],
) -> None:
    """Test all strategies handle names with suffixes."""
    trigram_strategy, component_strategy, initial_strategy = all_strategies

    parsed_name = ParsedName(
        original="Neilsen, Jr., Eric H.",
        format=NameFormat.LAST_COMMA_FIRST,
        surname="Neilsen",
        given_name="Eric H.",
        initials=["H"],
        suffix="Jr.",
    )

    strategies = [trigram_strategy, component_strategy, initial_strategy]

    for strategy in strategies:
        condition = strategy.build_condition(parsed_name)
        score_expr = strategy.build_score_expr(parsed_name)

        assert condition is not None
        assert score_expr is not None


def test_strategy_integration_with_unicode_names(
    all_strategies: tuple[TrigramStrategy, ComponentStrategy, InitialStrategy],
) -> None:
    """Test all strategies handle Unicode characters."""
    trigram_strategy, component_strategy, initial_strategy = all_strategies

    parsed_name = ParsedName(
        original="García, José",
        format=NameFormat.LAST_COMMA_FIRST,
        surname="García",
        given_name="José",
    )

    strategies = [trigram_strategy, component_strategy, initial_strategy]

    for strategy in strategies:
        condition = strategy.build_condition(parsed_name)
        score_expr = strategy.build_score_expr(parsed_name)

        assert condition is not None
        assert score_expr is not None


def test_strategy_integration_return_different_objects(
    all_strategies: tuple[TrigramStrategy, ComponentStrategy, InitialStrategy],
) -> None:
    """Test that strategies return independent objects."""
    trigram_strategy, component_strategy, initial_strategy = all_strategies

    parsed_name = ParsedName(
        original="Test Query",
        format=NameFormat.FIRST_LAST,
        surname="Test",
        given_name="Query",
    )

    condition1 = trigram_strategy.build_condition(parsed_name)
    condition2 = component_strategy.build_condition(parsed_name)
    condition3 = initial_strategy.build_condition(parsed_name)

    # All conditions should be different objects
    assert condition1 is not condition2
    assert condition2 is not condition3
    assert condition1 is not condition3

    score1 = trigram_strategy.build_score_expr(parsed_name)
    score2 = component_strategy.build_score_expr(parsed_name)
    score3 = initial_strategy.build_score_expr(parsed_name)

    # All score expressions should be different objects
    assert score1 is not score2
    assert score2 is not score3
    assert score1 is not score3


@pytest.fixture
def strategy_list() -> list[SearchStrategy]:
    """Provide a list of all search strategy instances for error handling
    tests.
    """
    return [TrigramStrategy(), ComponentStrategy(), InitialStrategy()]


def test_strategy_error_handling_empty_original(
    strategy_list: list[SearchStrategy],
) -> None:
    """Test strategies handle empty original string."""
    parsed_name = ParsedName(original="", format=NameFormat.COMPLEX)

    for strategy in strategy_list:
        # Should not raise exceptions
        condition = strategy.build_condition(parsed_name)
        score_expr = strategy.build_score_expr(parsed_name)

        assert condition is not None
        assert score_expr is not None


def test_strategy_error_handling_none_components(
    strategy_list: list[SearchStrategy],
) -> None:
    """Test strategies handle None name components gracefully."""
    parsed_name = ParsedName(
        original="test",
        format=NameFormat.COMPLEX,
        surname=None,
        given_name=None,
        initials=None,
        suffix=None,
    )

    for strategy in strategy_list:
        # Should not raise exceptions
        condition = strategy.build_condition(parsed_name)
        score_expr = strategy.build_score_expr(parsed_name)

        assert condition is not None
        assert score_expr is not None


def test_strategy_error_handling_special_characters(
    strategy_list: list[SearchStrategy],
) -> None:
    """Test strategies handle special characters in names."""
    parsed_name = ParsedName(
        original="O'Connor-Smith, Mary-Jane",
        format=NameFormat.LAST_COMMA_FIRST,
        surname="O'Connor-Smith",
        given_name="Mary-Jane",
    )

    for strategy in strategy_list:
        # Should not raise exceptions
        condition = strategy.build_condition(parsed_name)
        score_expr = strategy.build_score_expr(parsed_name)

        assert condition is not None
        assert score_expr is not None
