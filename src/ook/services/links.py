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
    SdmTableLinksCollection,
)
from ook.storage.linkstore import (
    LinkStore,
    SdmColumnLinksCollectionCursor,
    SdmLinksCollectionCursor,
    SdmTableLinksCollectionCursor,
)

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
        self,
        *,
        schema_name: str,
        limit: int | None = None,
        cursor: SdmTableLinksCollectionCursor | None = None,
    ) -> CountedPaginatedList[
        SdmTableLinksCollection, SdmTableLinksCollectionCursor
    ]:
        """Get links for all tables and columns scoped to an SDM schema."""
        return await self._link_store.get_table_links_for_sdm_schema(
            schema_name=schema_name,
            limit=limit,
            cursor=cursor,
        )

    async def get_sdm_links(
        self,
        *,
        include_schemas: bool,
        include_tables: bool,
        include_columns: bool,
        schema_name: str | None = None,
        limit: int | None = None,
        cursor: SdmLinksCollectionCursor | None = None,
    ) -> CountedPaginatedList[SdmLinksCollection, SdmLinksCollectionCursor]:
        """Get links for all SDM schemas."""
        return await self._link_store.get_sdm_links(
            include_schemas=include_schemas,
            include_tables=include_tables,
            include_columns=include_columns,
            schema_name=schema_name,
            limit=limit,
            cursor=cursor,
        )
