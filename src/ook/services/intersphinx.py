"""Service for the intersphinx inventory cache."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from structlog.stdlib import BoundLogger

from ook.domain.intersphinx import IntersphinxInventory, InventoryFetchStatus
from ook.storage.intersphinxstore import IntersphinxInventoryStore

__all__ = ["IntersphinxCacheService"]


class IntersphinxCacheService:
    """Service that serves cached Sphinx ``objects.inv`` inventories.

    This is the deep module for the intersphinx cache: `get_inventory` is
    the single entry point that resolves an origin inventory URL to its
    bytes, fetching from the origin and populating the cache on a miss. Any
    populated cache entry is served from Postgres without contacting
    upstream — a fetch within ``ttl`` is served as a fresh cache hit, an
    older one is served stale (proactive refresh is the background job's
    responsibility) — so the request path never depends on the origin once
    a copy exists. URL guarding and negative caching land in follow-on
    tasks.

    Parameters
    ----------
    http_client
        The shared HTTP client used to fetch origin inventories.
    inventory_store
        The store for cached inventories.
    ttl
        Freshness TTL: a cached inventory whose last fetch is within this
        window is served as a fresh hit; an older one is served stale.
    logger
        The logger.
    """

    def __init__(
        self,
        *,
        http_client: AsyncClient,
        inventory_store: IntersphinxInventoryStore,
        ttl: timedelta,
        logger: BoundLogger,
    ) -> None:
        self._http_client = http_client
        self._inventory_store = inventory_store
        self._ttl = ttl
        self._logger = logger

    async def get_inventory(self, url: str) -> IntersphinxInventory:
        """Resolve an origin inventory URL to its cached record.

        On a cold miss the origin is fetched synchronously, stored, and
        returned. When the URL is already cached with content, the stored
        copy is served without contacting upstream and its last-requested
        time is bumped — a fetch within the TTL is a fresh cache hit, an
        older one is served stale.

        Parameters
        ----------
        url
            The full origin ``objects.inv`` URL.

        Returns
        -------
        IntersphinxInventory
            The cached inventory record for the URL.
        """
        cached = await self._inventory_store.get_inventory(url)
        if cached is not None and cached.content is not None:
            now = datetime.now(tz=UTC)
            await self._inventory_store.touch_date_requested(url, now=now)
            self._log_cache_serve(cached, now=now)
            return replace(cached, date_requested=now)
        return await self._fetch_and_store(url)

    def _log_cache_serve(
        self, inventory: IntersphinxInventory, *, now: datetime
    ) -> None:
        """Emit a structured cache-hit or stale-serve log for a served copy.

        A copy fetched within the TTL is a fresh hit; a copy with no fetch
        time or a fetch older than the TTL is served stale.
        """
        is_fresh = (
            inventory.date_fetched is not None
            and now - inventory.date_fetched <= self._ttl
        )
        if is_fresh:
            self._logger.info(
                "Serving fresh intersphinx inventory from cache",
                url=inventory.url,
                cache_status="hit",
            )
        else:
            self._logger.info(
                "Serving stale intersphinx inventory from cache",
                url=inventory.url,
                cache_status="stale",
            )

    async def _fetch_and_store(self, url: str) -> IntersphinxInventory:
        """Fetch an origin inventory and store it (the cold-miss path)."""
        self._logger.info(
            "Fetching intersphinx inventory on cache miss", url=url
        )
        response = await self._http_client.get(url)
        response.raise_for_status()
        now = datetime.now(tz=UTC)
        inventory = IntersphinxInventory(
            url=url,
            content=response.content,
            content_type=response.headers.get("Content-Type"),
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            date_fetched=now,
            date_requested=now,
            last_fetch_status=InventoryFetchStatus.success,
            last_fetch_error=None,
        )
        await self._inventory_store.upsert_inventory(inventory)
        return inventory
