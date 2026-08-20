"""Tests for the application configuration."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from ook.config import Configuration, config


def test_intersphinx_ttl_default() -> None:
    """OOK_INTERSPHINX_TTL defaults to one hour when unset."""
    assert config.intersphinx_ttl == timedelta(hours=1)


def test_intersphinx_negative_ttl_default() -> None:
    """OOK_INTERSPHINX_NEGATIVE_TTL defaults to five minutes when unset."""
    assert config.intersphinx_negative_ttl == timedelta(minutes=5)


def test_intersphinx_active_window_default() -> None:
    """OOK_INTERSPHINX_ACTIVE_WINDOW defaults to thirty days when unset."""
    assert config.intersphinx_active_window == timedelta(days=30)


def test_oidc_audience_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OOK_OIDC_AUDIENCE supplies the required GitHub OIDC audience."""
    monkeypatch.setenv("OOK_OIDC_AUDIENCE", "https://other.example.org/ook")
    assert Configuration().oidc_audience == "https://other.example.org/ook"


def test_oidc_audience_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """OOK_OIDC_AUDIENCE has no default.

    Every environment's audience is its own public base URL, and a shared
    default would let a token minted for one deployment verify against
    another — so the setting is required rather than guessed.
    """
    monkeypatch.delenv("OOK_OIDC_AUDIENCE", raising=False)
    with pytest.raises(ValidationError):
        Configuration()
