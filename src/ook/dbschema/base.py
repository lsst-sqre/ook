"""Base class for SQLAlchemy ORM model of database schema."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

__all__ = ["Base"]


class Base(DeclarativeBase):
    """Declarative base for SQLAlchemy ORM model of database schema."""
