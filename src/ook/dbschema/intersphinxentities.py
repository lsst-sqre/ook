"""Database schema for entities parsed out of Sphinx object inventories.

One table holds every object Ook models from every documentation source,
whatever its Sphinx domain: a row's identity is the pair
``(sphinx_domain, name)``, because that pair is exactly what a Sphinx
cross-reference resolves against. Two sites documenting the same object
therefore share one entity and contribute a link each, which is what makes
"where is this documented?" a single-row question.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, ForeignKey, UnicodeText, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .links import SqlIntersphinxLink

__all__ = ["SqlIntersphinxEntity"]


class SqlIntersphinxEntity(Base):
    """A SQLAlchemy model for one documented object in a Sphinx domain."""

    __tablename__ = "intersphinx_entity"

    __table_args__ = (
        UniqueConstraint(
            "sphinx_domain", "name", name="uq_intersphinx_entity_name"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    sphinx_domain: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The Sphinx domain the object was declared in (``py``, ``std``, ...).

    This is the domain half of a cross-reference role like ``py:class``,
    not a hostname, and it is half of the row's identity: nothing stops
    two Sphinx domains from naming different objects the same thing.
    """

    name: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The object's fully qualified name within its Sphinx domain."""

    role: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The Sphinx role the object was declared with (``class``,
    ``method``, ...).

    Descriptive rather than identifying: the role is not part of the
    unique constraint, so a source that changes an object's role updates
    the entity instead of forking it.
    """

    display_name: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The name to display for the object."""

    parent_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("intersphinx_entity.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    """The entity that contains this one, or null when it has none.

    Self-referential because containment in a Sphinx domain is a tree over
    the same kind of thing: a module holds classes, a class holds methods.
    The inventory format records no containment of its own, so this is
    derived: the naming strategy for the row's Sphinx domain names the
    containing entity -- see `ook.domain.intersphinxentities` -- and the
    whole column is recomputed from the links every source currently
    contributes each time those links change.

    Null carries both kinds of top level and deliberately does not
    distinguish them: an object whose domain says it has no parent, and one
    whose parent no source documents. ``ON DELETE SET NULL`` is a backstop
    rather than a working path -- an entity nothing documents is never
    anybody's parent by the time it is pruned -- and leaves a child top
    level rather than dangling if one is ever deleted out from under it.
    """

    extras: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    """Domain-specific attributes that have no column of their own, or null
    when the entity has none.

    The escape hatch that lets this table absorb entity kinds richer than a
    Sphinx object -- SDM's typed sort keys and Felis IDs, for instance --
    without a migration per kind. Nothing writes it yet.
    """

    parent: Mapped[SqlIntersphinxEntity | None] = relationship(
        "SqlIntersphinxEntity",
        back_populates="children",
        remote_side="SqlIntersphinxEntity.id",
    )
    """The entity that contains this one."""

    children: Mapped[list[SqlIntersphinxEntity]] = relationship(
        "SqlIntersphinxEntity", back_populates="parent"
    )
    """The entities this one contains."""

    links: Mapped[list[SqlIntersphinxLink]] = relationship(
        "SqlIntersphinxLink",
        back_populates="entity",
        cascade="all, delete-orphan",
    )
    """The documentation links to this entity."""
