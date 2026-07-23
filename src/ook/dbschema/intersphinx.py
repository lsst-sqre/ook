"""Database schema for the intersphinx inventory cache.

This domain is standalone: the cache is keyed solely by the full origin
``objects.inv`` URL and has no foreign keys into other domains.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, LargeBinary, UnicodeText
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

__all__ = ["SqlIntersphinxInventory"]


class SqlIntersphinxInventory(Base):
    """A SQLAlchemy model for a cached Sphinx ``objects.inv`` inventory.

    Each row caches one origin inventory keyed by its full URL. A row whose
    ``content`` is null and whose ``last_fetch_status`` is ``failure``
    doubles as the negative cache for a cold-miss fetch failure.
    """

    __tablename__ = "intersphinx_inventory"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    """The primary key."""

    url: Mapped[str] = mapped_column(
        UnicodeText, nullable=False, index=True, unique=True
    )
    """The full origin ``objects.inv`` URL (the unique cache key).

    RTD-style versioned URLs are naturally distinct because the full URL is
    the key.
    """

    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    """The cached inventory bytes, or null for a failure-only (negative
    cache) row.
    """

    content_type: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True
    )
    """The stored ``Content-Type`` of the inventory, if known."""

    etag: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    """The upstream ``ETag`` from the last successful fetch, for
    conditional ``If-None-Match`` revalidation.
    """

    last_modified: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True
    )
    """The upstream ``Last-Modified`` header from the last successful
    fetch, for conditional ``If-Modified-Since`` revalidation.
    """

    date_fetched: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    """The time of the last upstream fetch attempt (the freshness anchor),
    or null if the row has never been fetched.
    """

    date_requested: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    """The time of the most recent client request for this inventory."""

    last_fetch_status: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True
    )
    """The outcome of the last upstream fetch attempt
    (`ook.domain.intersphinx.InventoryFetchStatus` value), or null if the
    row was created without a fetch attempt.
    """

    last_fetch_error: Mapped[str | None] = mapped_column(
        UnicodeText, nullable=True
    )
    """A description of the last fetch failure, or null if the last fetch
    succeeded.
    """
