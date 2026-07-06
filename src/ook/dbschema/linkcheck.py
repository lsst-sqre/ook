"""Database schema for the link-check domain.

This domain is standalone: it has no foreign keys into the SDM ``link``
or bibliography ``resource``/``external_reference`` tables. Future
correlation with those domains happens by URL matching.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    UnicodeText,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

__all__ = [
    "SqlCheckedUrl",
    "SqlLinkCheck",
    "SqlLinkCheckUrl",
    "SqlUrlOccurrence",
]


class SqlCheckedUrl(Base):
    """A SQLAlchemy model for the health record of a checked URL.

    Each row tracks one canonical (fragment-stripped) URL across checks,
    builds, and origins. The state columns mirror the
    `ook.domain.linkcheck.LinkState` domain model; the state columns are
    null until the URL has been checked for the first time.
    """

    __tablename__ = "checked_url"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    url: Mapped[str] = mapped_column(
        UnicodeText, nullable=False, index=True, unique=True
    )
    """The canonical (fragment-stripped) URL."""

    status: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True, index=True
    )
    """The current health status (`ook.domain.linkcheck.LinkStatus`
    value), or null if the URL has never been checked.
    """

    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """The final HTTP status code from the most recent check."""

    redirect_url: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True
    )
    """The final resolved location, if the most recent check succeeded
    via a redirect.
    """

    redirect_status_code: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    """The HTTP status code of the redirect (permanence: 301/308 are
    permanent), if the most recent check succeeded via a redirect.
    """

    error: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """A description of the failure from the most recent check."""

    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    """The time of the most recent check, or null if never checked."""

    last_ok_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """The time the URL last resolved successfully."""

    failing_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """The start of the current consecutive-failure streak (the
    first-failed timestamp), or null if the URL is not failing.
    """

    failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    """The number of consecutive failed checks in the current streak."""

    next_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    """The time of the next scheduled recheck on the retry ladder, or
    null if the URL is not on the ladder.
    """

    check_method: Mapped[str] = mapped_column(
        UnicodeText, nullable=False, default="http"
    )
    """The method used to check this URL.

    Reserved for a future Playwright/headless-browser checker; only
    ``http`` is used today.
    """

    date_created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """The date this record was created."""

    occurrences: Mapped[list[SqlUrlOccurrence]] = relationship(
        "SqlUrlOccurrence",
        back_populates="checked_url",
        cascade="all, delete-orphan",
    )
    """The origin-page occurrences of this URL."""


class SqlUrlOccurrence(Base):
    """A SQLAlchemy model for an occurrence of a checked URL on a page
    of an origin website.

    An origin's occurrence set is replaced wholesale when a
    default-version submission arrives.
    """

    __tablename__ = "url_occurrence"

    __table_args__ = (
        UniqueConstraint(
            "origin_base_url",
            "origin_path",
            "checked_url_id",
            name="uq_url_occurrence",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    origin_base_url: Mapped[str] = mapped_column(
        UnicodeText, nullable=False, index=True
    )
    """The origin website's normalized base URL."""

    origin_path: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    """The page path where the URL occurs, relative to the origin's
    base URL.
    """

    checked_url_id: Mapped[int] = mapped_column(
        ForeignKey("checked_url.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    """The checked URL that occurs on the page."""

    checked_url: Mapped[SqlCheckedUrl] = relationship(
        "SqlCheckedUrl", back_populates="occurrences"
    )
    """The checked URL."""


class SqlLinkCheck(Base):
    """A SQLAlchemy model for a submitted link-check request.

    A check associates one submission (one documentation build) with the
    set of URLs it asked Ook to validate, via the ``linkcheck_check_url``
    membership table.
    """

    __tablename__ = "linkcheck_check"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    origin_base_url: Mapped[str] = mapped_column(
        UnicodeText, nullable=False, index=True
    )
    """The normalized base URL of the origin website the check was
    submitted for.
    """

    is_default_version: Mapped[bool] = mapped_column(Boolean, nullable=False)
    """Whether the submission is a build of the origin's default version
    (only those replace the origin's occurrence set).
    """

    status: Mapped[str] = mapped_column(
        UnicodeText, nullable=False, default="pending"
    )
    """The processing status of the check (``pending``,
    ``in_progress``, or ``complete``).
    """

    date_created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    """The date the check was submitted (indexed for expiry purges)."""

    date_completed: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """The date the check completed, or null while pending."""

    urls: Mapped[list[SqlLinkCheckUrl]] = relationship(
        "SqlLinkCheckUrl",
        back_populates="check",
        cascade="all, delete-orphan",
    )
    """The URL memberships of this check."""


class SqlLinkCheckUrl(Base):
    """A SQLAlchemy model for the many-to-many membership of checked
    URLs in a submitted link check.
    """

    __tablename__ = "linkcheck_check_url"

    check_id: Mapped[int] = mapped_column(
        ForeignKey("linkcheck_check.id", ondelete="CASCADE"),
        primary_key=True,
    )
    """The link check this membership belongs to."""

    checked_url_id: Mapped[int] = mapped_column(
        ForeignKey("checked_url.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    """The checked URL that is a member of the check."""

    check: Mapped[SqlLinkCheck] = relationship(
        "SqlLinkCheck", back_populates="urls"
    )
    """The link check."""

    checked_url: Mapped[SqlCheckedUrl] = relationship("SqlCheckedUrl")
    """The checked URL."""
