"""The documentation links service."""

from __future__ import annotations

from safir.database import CountedPaginatedList
from structlog.stdlib import BoundLogger

from ook.domain.links import (
    SdmColumnLink,
    SdmColumnLinksCollection,
    SdmLinksCollection,
    SdmSchemaLink,
    SdmTableLink,
)
from ook.storage.linkstore import LinkStore, SdmColumnLinksCollectionCursor

__all__ = ["LinksService"]


class LinksService:
    """A service for linking to documentation across known domains."""

    def __init__(self, logger: BoundLogger, link_store: LinkStore) -> None:
        self._logger = logger
        self._link_store = link_store

    async def get_links_for_sdm_schema(
        self, schema_name: str
    ) -> list[SdmSchemaLink] | None:
        """Get links for an SDM schema."""
        links = await self._link_store.get_links_for_sdm_schema(schema_name)
        if len(links) == 0:
            return None
        return links

    async def get_links_for_sdm_table(
        self, *, schema_name: str, table_name: str
    ) -> list[SdmTableLink] | None:
        """Get links for an SDM table."""
        links = await self._link_store.get_links_for_sdm_table(
            schema_name=schema_name, table_name=table_name
        )
        if len(links) == 0:
            return None
        return links

    async def get_links_for_sdm_column(
        self, *, schema_name: str, table_name: str, column_name: str
    ) -> list[SdmColumnLink] | None:
        """Get links for an SDM column."""
        links = await self._link_store.get_links_for_sdm_column(
            column_name=column_name,
            schema_name=schema_name,
            table_name=table_name,
        )
        if len(links) == 0:
            return None
        return links

    async def get_column_links_for_sdm_table(
        self,
        *,
        schema_name: str,
        table_name: str,
        limit: int | None = None,
        cursor: SdmColumnLinksCollectionCursor | None = None,
    ) -> CountedPaginatedList[
        SdmColumnLinksCollection, SdmColumnLinksCollectionCursor
    ]:
        """Get links for all columns scoped to an SDM table."""
        return await self._link_store.get_column_links_for_sdm_table(
            schema_name=schema_name,
            table_name=table_name,
            limit=limit,
            cursor=cursor,
        )

    async def get_table_links_for_sdm_schema(
        self, *, schema_name: str, include_columns: bool
    ) -> list[SdmLinksCollection] | None:
        """Get links for all tables scoped to an SDM schema."""
        sdm_entities_link_collection = (
            await self._link_store.get_sdm_links_scoped_to_schema(
                schema_name=schema_name,
                include_columns=include_columns,
            )
        )
        if len(sdm_entities_link_collection) == 0:
            return None
        return sdm_entities_link_collection

    async def get_sdm_links(
        self, *, include_tables: bool, include_columns: bool
    ) -> list[SdmLinksCollection] | None:
        """Get links for all SDM schemas."""
        sdm_entities_link_collection = await self._link_store.get_sdm_links(
            include_tables=include_tables,
            include_columns=include_columns,
        )
        if len(sdm_entities_link_collection) == 0:
            return None
        return sdm_entities_link_collection
