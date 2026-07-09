"""Service for the intersphinx inventory cache."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from httpx import AsyncClient
from structlog.stdlib import BoundLogger

from ook.domain.intersphinx import IntersphinxInventory, InventoryFetchStatus
from ook.storage.intersphinxstore import IntersphinxInventoryStore

__all__ = ["IntersphinxCacheService"]


class IntersphinxCacheService:
    """Service that serves cached Sphinx ``objects.inv`` inventories.

    This is the deep module for the intersphinx cache: `get_inventory` is
    the single entry point that resolves an origin inventory URL to its
    bytes, fetching from the origin and populating the cache on a miss. TTL
    revalidation, stale serving, URL guarding, and negative caching land in
    follow-on tasks; for now it implements the cold-miss path and serves a
    populated cache entry as-is.

    Parameters
    ----------
    http_client
        The shared HTTP client used to fetch origin inventories.
    inventory_store
        The store for cached inventories.
    logger
        The logger.
    """

    def __init__(
        self,
        *,
        http_client: AsyncClient,
        inventory_store: IntersphinxInventoryStore,
        logger: BoundLogger,
    ) -> None:
        self._http_client = http_client
        self._inventory_store = inventory_store
        self._logger = logger

    async def get_inventory(self, url: str) -> IntersphinxInventory:
        """Resolve an origin inventory URL to its cached record.

        On a cold miss the origin is fetched synchronously, stored, and
        returned. When the URL is already cached with content, the stored
        copy is served and its last-requested time is bumped.

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
            return replace(cached, date_requested=now)
        return await self._fetch_and_store(url)

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
