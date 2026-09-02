"""Database schema for the registry of intersphinx documentation sources.

A source is one documentation site Ook ingests objects from, identified by
the URL of the ``objects.inv`` inventory it publishes. The registry is
deliberately separate from the `ook.dbschema.intersphinx` inventory cache:
the cache is keyed by whatever URL anyone asked for, while a source is a
site an operator has asked Ook to index, and the two sets only overlap by
coincidence.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, UnicodeText
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .links import SqlIntersphinxLink

__all__ = ["SqlIntersphinxSource"]


class SqlIntersphinxSource(Base):
    """A SQLAlchemy model for a registered intersphinx documentation source.

    Each row is one documentation site, keyed by its inventory URL. Only the
    canonical version of a site is registered -- there is no version
    dimension here, because a row is a place to link readers to rather than
    a build to archive.
    """

    __tablename__ = "intersphinx_source"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    url: Mapped[str] = mapped_column(
        UnicodeText, nullable=False, index=True, unique=True
    )
    """The full URL of the site's ``objects.inv`` inventory.

    This is the source's identity, so registering the same inventory twice
    is a conflict rather than a second row.
    """

    title: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The human title of the documentation site.

    This surfaces as the ``collection_title`` of every link ingested from
    the source, so it is what a reader sees naming the site a link goes to.
    """

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    """Whether ingest runs visit this source.

    Disabling is not deleting: a disabled source keeps its row and its
    links, so a site can be parked without losing the links already served
    from it.
    """

    date_ingested: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """The time of the most recent ingest attempt, successful or not, or
    null if the source has never been ingested.

    Named for Ook's ``date_`` prefix convention for date-valued fields
    rather than the ``last_ingested_at`` of the design note.
    """

    last_status: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """The outcome of the most recent ingest attempt
    (`ook.domain.intersphinxsources.SourceIngestStatus` value), or null if
    the source has never been ingested.
    """

    last_error: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """A description of the most recent ingest failure, or null when the
    last attempt succeeded or none has been made.
    """

    links: Mapped[list[SqlIntersphinxLink]] = relationship(
        "SqlIntersphinxLink", back_populates="source"
    )
    """The links ingested from this source."""
