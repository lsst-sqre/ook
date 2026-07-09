"""Tests for the application configuration."""

from __future__ import annotations

from datetime import timedelta

from ook.config import config


def test_intersphinx_ttl_default() -> None:
    """OOK_INTERSPHINX_TTL defaults to one hour when unset."""
    assert config.intersphinx_ttl == timedelta(hours=1)
