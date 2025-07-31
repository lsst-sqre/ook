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


class TestSearchStrategy:
    """Test the SearchStrategy abstract base class."""

    def test_abstract_base_class(self) -> None:
        """Test that SearchStrategy cannot be instantiated directly."""
        with pytest.raises(TypeError):
            SearchStrategy()  # type: ignore[abstract]

    def test_mock_strategy_instantiation(self) -> None:
        """Test that concrete implementations can be instantiated."""
        strategy = MockStrategy()
        assert isinstance(strategy, SearchStrategy)

    def test_abstract_methods_required(self) -> None:
        """Test that abstract methods must be implemented."""
        parsed_name = ParsedName(
            original="Test Query", format=NameFormat.FIRST_LAST
        )

        strategy = MockStrategy()

        # Should not raise since MockStrategy implements the abstract methods
        strategy.build_condition(parsed_name)
        strategy.build_score_expr(parsed_name)


class TestTrigramStrategy:
    """Test the TrigramStrategy implementation."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.strategy = TrigramStrategy()
        self.custom_strategy = TrigramStrategy(similarity_threshold=0.2)

    def test_initialization_default_threshold(self) -> None:
        """Test default initialization."""
        strategy = TrigramStrategy()
        assert strategy.similarity_threshold == 0.1

    def test_initialization_custom_threshold(self) -> None:
        """Test initialization with custom threshold."""
        strategy = TrigramStrategy(similarity_threshold=0.3)
        assert strategy.similarity_threshold == 0.3

    def test_build_condition_structure(self) -> None:
        """Test that build_condition returns proper structure."""
        parsed_name = ParsedName(
            original="Jonathan Sick", format=NameFormat.FIRST_LAST
        )

        condition = self.strategy.build_condition(parsed_name)

        # Should return a comparison expression
        assert hasattr(condition, "left")
        assert hasattr(condition, "right")
        assert hasattr(condition, "operator")

    def test_build_score_expr_structure(self) -> None:
        """Test that build_score_expr returns proper structure."""
        parsed_name = ParsedName(
            original="Jonathan Sick", format=NameFormat.FIRST_LAST
        )

        score_expr = self.strategy.build_score_expr(parsed_name)

        # Should return a column element
        assert isinstance(score_expr, ColumnElement)

    def test_different_thresholds_affect_condition(self) -> None:
        """Test that different thresholds create different conditions."""
        parsed_name = ParsedName(
            original="Test Query", format=NameFormat.FIRST_LAST
        )

        condition1 = self.strategy.build_condition(parsed_name)
        condition2 = self.custom_strategy.build_condition(parsed_name)

        # The conditions should have different threshold values
        # (though we can't easily test the actual SQL without compilation)
        assert condition1 is not condition2

    def test_handles_different_name_formats(self) -> None:
        """Test strategy handles different name formats."""
        test_cases = [
            ParsedName(
                original="Sick, Jonathan", format=NameFormat.LAST_COMMA_FIRST
            ),
            ParsedName(
                original="Sick, J", format=NameFormat.LAST_COMMA_INITIAL
            ),
            ParsedName(original="Sick", format=NameFormat.SINGLE_WORD),
            ParsedName(original="Jonathan Sick", format=NameFormat.FIRST_LAST),
        ]

        for parsed_name in test_cases:
            # Should not raise exceptions
            condition = self.strategy.build_condition(parsed_name)
            score_expr = self.strategy.build_score_expr(parsed_name)

            assert condition is not None
            assert score_expr is not None


class TestComponentStrategy:
    """Test the ComponentStrategy implementation."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.strategy = ComponentStrategy()

    def test_build_condition_with_surname_only(self) -> None:
        """Test condition building with surname only."""
        parsed_name = ParsedName(
            original="Sick", format=NameFormat.SINGLE_WORD, surname="Sick"
        )

        condition = self.strategy.build_condition(parsed_name)
        assert condition is not None

    def test_build_condition_with_given_name_only(self) -> None:
        """Test condition building with given name only."""
        parsed_name = ParsedName(
            original="Jonathan",
            format=NameFormat.SINGLE_WORD,
            given_name="Jonathan",
        )

        condition = self.strategy.build_condition(parsed_name)
        assert condition is not None

    def test_build_condition_with_both_names(self) -> None:
        """Test condition building with both surname and given name."""
        parsed_name = ParsedName(
            original="Jonathan Sick",
            format=NameFormat.FIRST_LAST,
            surname="Sick",
            given_name="Jonathan",
        )

        condition = self.strategy.build_condition(parsed_name)
        assert condition is not None

    def test_build_condition_with_middle_initial(self) -> None:
        """Test condition building with middle initial."""
        parsed_name = ParsedName(
            original="John A Smith",
            format=NameFormat.FIRST_LAST,
            surname="Smith",
            given_name="John A",
            initials=["A"],
        )

        condition = self.strategy.build_condition(parsed_name)
        assert condition is not None

    def test_build_condition_empty_name(self) -> None:
        """Test condition building with empty name components."""
        parsed_name = ParsedName(original="", format=NameFormat.COMPLEX)

        condition = self.strategy.build_condition(parsed_name)
        # Should return a non-null condition even for empty names
        assert condition is not None

    def test_build_score_expr_with_complete_name(self) -> None:
        """Test score expression with complete name."""
        parsed_name = ParsedName(
            original="Jonathan Sick",
            format=NameFormat.FIRST_LAST,
            surname="Sick",
            given_name="Jonathan",
        )

        score_expr = self.strategy.build_score_expr(parsed_name)
        assert isinstance(score_expr, ColumnElement)

    def test_build_score_expr_with_middle_initial(self) -> None:
        """Test score expression handles middle initial extraction."""
        parsed_name = ParsedName(
            original="John A Smith",
            format=NameFormat.FIRST_LAST,
            surname="Smith",
            given_name="John A",
            initials=["A"],
        )

        score_expr = self.strategy.build_score_expr(parsed_name)
        assert isinstance(score_expr, ColumnElement)

    def test_build_score_expr_incomplete_name(self) -> None:
        """Test score expression with incomplete name components."""
        parsed_name = ParsedName(
            original="Sick", format=NameFormat.SINGLE_WORD, surname="Sick"
        )

        score_expr = self.strategy.build_score_expr(parsed_name)
        assert isinstance(score_expr, ColumnElement)


class TestInitialStrategy:
    """Test the InitialStrategy implementation."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.strategy = InitialStrategy()

    def test_build_condition_with_initials(self) -> None:
        """Test condition building with initials."""
        parsed_name = ParsedName(
            original="Sick, J",
            format=NameFormat.LAST_COMMA_INITIAL,
            surname="Sick",
            initials=["J"],
        )

        condition = self.strategy.build_condition(parsed_name)
        assert condition is not None

    def test_build_condition_with_multiple_initials(self) -> None:
        """Test condition building with multiple initials."""
        parsed_name = ParsedName(
            original="García, M. J.",
            format=NameFormat.LAST_COMMA_INITIAL,
            surname="García",
            initials=["M", "J"],
        )

        condition = self.strategy.build_condition(parsed_name)
        assert condition is not None

    def test_build_condition_without_initials(self) -> None:
        """Test condition building without initials."""
        parsed_name = ParsedName(
            original="Jonathan Sick",
            format=NameFormat.FIRST_LAST,
            surname="Sick",
            given_name="Jonathan",
        )

        condition = self.strategy.build_condition(parsed_name)
        # Should return a condition that matches nothing
        assert condition is not None

    def test_build_condition_initials_with_surname(self) -> None:
        """Test condition building with both initials and surname."""
        parsed_name = ParsedName(
            original="Smith, J. K.",
            format=NameFormat.LAST_COMMA_INITIAL,
            surname="Smith",
            initials=["J", "K"],
        )

        condition = self.strategy.build_condition(parsed_name)
        assert condition is not None

    def test_build_score_expr_with_initials(self) -> None:
        """Test score expression with initials."""
        parsed_name = ParsedName(
            original="Sick, J",
            format=NameFormat.LAST_COMMA_INITIAL,
            surname="Sick",
            initials=["J"],
        )

        score_expr = self.strategy.build_score_expr(parsed_name)
        assert isinstance(score_expr, ColumnElement)

    def test_build_score_expr_without_initials(self) -> None:
        """Test score expression without initials."""
        parsed_name = ParsedName(
            original="Jonathan Sick",
            format=NameFormat.FIRST_LAST,
            surname="Sick",
            given_name="Jonathan",
        )

        score_expr = self.strategy.build_score_expr(parsed_name)
        assert isinstance(score_expr, ColumnElement)

    def test_build_score_expr_initials_no_surname(self) -> None:
        """Test score expression with initials but no surname."""
        parsed_name = ParsedName(
            original="J", format=NameFormat.LAST_COMMA_INITIAL, initials=["J"]
        )

        score_expr = self.strategy.build_score_expr(parsed_name)
        assert isinstance(score_expr, ColumnElement)


class TestStrategyIntegration:
    """Test integration between strategies and parsed names."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.trigram_strategy = TrigramStrategy()
        self.component_strategy = ComponentStrategy()
        self.initial_strategy = InitialStrategy()

    def test_strategies_with_compound_surname(self) -> None:
        """Test all strategies handle compound surnames."""
        parsed_name = ParsedName(
            original="Plazas Malagón, Andrés A.",
            format=NameFormat.LAST_COMMA_FIRST,
            surname="Plazas Malagón",
            given_name="Andrés A.",
            initials=["A"],
        )

        # All strategies should handle this without errors
        strategies = [
            self.trigram_strategy,
            self.component_strategy,
            self.initial_strategy,
        ]

        for strategy in strategies:
            condition = strategy.build_condition(parsed_name)
            score_expr = strategy.build_score_expr(parsed_name)

            assert condition is not None
            assert score_expr is not None

    def test_strategies_with_suffix(self) -> None:
        """Test all strategies handle names with suffixes."""
        parsed_name = ParsedName(
            original="Neilsen, Jr., Eric H.",
            format=NameFormat.LAST_COMMA_FIRST,
            surname="Neilsen",
            given_name="Eric H.",
            initials=["H"],
            suffix="Jr.",
        )

        strategies = [
            self.trigram_strategy,
            self.component_strategy,
            self.initial_strategy,
        ]

        for strategy in strategies:
            condition = strategy.build_condition(parsed_name)
            score_expr = strategy.build_score_expr(parsed_name)

            assert condition is not None
            assert score_expr is not None

    def test_strategies_with_unicode_names(self) -> None:
        """Test all strategies handle Unicode characters."""
        parsed_name = ParsedName(
            original="García, José",
            format=NameFormat.LAST_COMMA_FIRST,
            surname="García",
            given_name="José",
        )

        strategies = [
            self.trigram_strategy,
            self.component_strategy,
            self.initial_strategy,
        ]

        for strategy in strategies:
            condition = strategy.build_condition(parsed_name)
            score_expr = strategy.build_score_expr(parsed_name)

            assert condition is not None
            assert score_expr is not None

    def test_strategies_return_different_objects(self) -> None:
        """Test that strategies return independent objects."""
        parsed_name = ParsedName(
            original="Test Query",
            format=NameFormat.FIRST_LAST,
            surname="Test",
            given_name="Query",
        )

        condition1 = self.trigram_strategy.build_condition(parsed_name)
        condition2 = self.component_strategy.build_condition(parsed_name)
        condition3 = self.initial_strategy.build_condition(parsed_name)

        # All conditions should be different objects
        assert condition1 is not condition2
        assert condition2 is not condition3
        assert condition1 is not condition3

        score1 = self.trigram_strategy.build_score_expr(parsed_name)
        score2 = self.component_strategy.build_score_expr(parsed_name)
        score3 = self.initial_strategy.build_score_expr(parsed_name)

        # All score expressions should be different objects
        assert score1 is not score2
        assert score2 is not score3
        assert score1 is not score3


class TestStrategyErrorHandling:
    """Test error handling in search strategies."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.strategies = [
            TrigramStrategy(),
            ComponentStrategy(),
            InitialStrategy(),
        ]

    def test_strategies_handle_empty_original(self) -> None:
        """Test strategies handle empty original string."""
        parsed_name = ParsedName(original="", format=NameFormat.COMPLEX)

        for strategy in self.strategies:
            # Should not raise exceptions
            condition = strategy.build_condition(parsed_name)
            score_expr = strategy.build_score_expr(parsed_name)

            assert condition is not None
            assert score_expr is not None

    def test_strategies_handle_none_components(self) -> None:
        """Test strategies handle None name components gracefully."""
        parsed_name = ParsedName(
            original="test",
            format=NameFormat.COMPLEX,
            surname=None,
            given_name=None,
            initials=None,
            suffix=None,
        )

        for strategy in self.strategies:
            # Should not raise exceptions
            condition = strategy.build_condition(parsed_name)
            score_expr = strategy.build_score_expr(parsed_name)

            assert condition is not None
            assert score_expr is not None

    def test_strategies_handle_special_characters(self) -> None:
        """Test strategies handle special characters in names."""
        parsed_name = ParsedName(
            original="O'Connor-Smith, Mary-Jane",
            format=NameFormat.LAST_COMMA_FIRST,
            surname="O'Connor-Smith",
            given_name="Mary-Jane",
        )

        for strategy in self.strategies:
            # Should not raise exceptions
            condition = strategy.build_condition(parsed_name)
            score_expr = strategy.build_score_expr(parsed_name)

            assert condition is not None
            assert score_expr is not None
