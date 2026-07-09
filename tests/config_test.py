"""Tests for the application configuration."""

from __future__ import annotations

from datetime import timedelta

from ook.config import config


def test_intersphinx_ttl_default() -> None:
    """OOK_INTERSPHINX_TTL defaults to one hour when unset."""
    assert config.intersphinx_ttl == timedelta(hours=1)


def test_intersphinx_negative_ttl_default() -> None:
    """OOK_INTERSPHINX_NEGATIVE_TTL defaults to five minutes when unset."""
    assert config.intersphinx_negative_ttl == timedelta(minutes=5)


def test_intersphinx_active_window_default() -> None:
    """OOK_INTERSPHINX_ACTIVE_WINDOW defaults to thirty days when unset."""
    assert config.intersphinx_active_window == timedelta(days=30)
