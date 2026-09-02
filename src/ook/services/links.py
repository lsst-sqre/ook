"""The documentation links service."""

from __future__ import annotations

from safir.database import CountedPaginatedList
from structlog.stdlib import BoundLogger

from ook.domain.intersphinxentities import (
    PYTHON_SPHINX_DOMAIN,
    IntersphinxEntityLinks,
)
from ook.domain.links import (
    SdmColumnLink,
    SdmColumnLinksCollection,
    SdmLinksCollection,
    SdmSchemaLink,
    SdmTableLink,
    SdmTableLinksCollection,
)
from ook.storage.intersphinxentitystore import IntersphinxEntityStore
from ook.storage.linkstore import (
    LinkStore,
    SdmColumnLinksCollectionCursor,
    SdmLinksCollectionCursor,
    SdmTableLinksCollectionCursor,
)

__all__ = ["LinksService"]


class LinksService:
    """A service for linking to documentation across known domains."""

    def __init__(
        self,
        logger: BoundLogger,
        link_store: LinkStore,
        entity_store: IntersphinxEntityStore,
    ) -> None:
        self._logger = logger
        self._link_store = link_store
        self._entity_store = entity_store

    async def get_python_object(
        self, name: str
    ) -> IntersphinxEntityLinks | None:
        """Get a Python object and the documentation links to it.

        The entity is returned even when no source links to it, because
        such an entity is not a gap in the data: it is a name held in place
        by the documented objects beneath it -- a package whose own page no
        site publishes. Only a name nothing in the ``py`` domain answers to
        is absent, which is what lets the API distinguish "unknown object"
        from "known object nobody gave a page".

        Parameters
        ----------
        name
            The object's fully qualified Python name.

        Returns
        -------
        IntersphinxEntityLinks or None
            The object with the links every source contributed for it, or
            None if no stored Python object goes by that name.
        """
        return await self._entity_store.get_entity(PYTHON_SPHINX_DOMAIN, name)

    async def get_links_for_sdm_schema(
        self, schema_name: str
    ) -> list[SdmSchemaLink] | None:
        """Get links for documentation an SDM schema.

        Parameters
        ----------
        schema_name
            The name of the schema.

        Returns
        -------
        list | None
            A list of links about the SDM (`SdmSchemaLink`), or `None` if no
            links are found.
        """
        links = await self._link_store.get_links_for_sdm_schema(schema_name)
        if len(links) == 0:
            return None
        return links

    async def get_links_for_sdm_table(
        self, *, schema_name: str, table_name: str
    ) -> list[SdmTableLink] | None:
        """Get links for documentation on an SDM table.

        Parameters
        ----------
        schema_name
            The name of the schema.
        table_name
            The name of the table.

        Returns
        -------
        list | None
            A list of links about the SDM (`SdmTableLink`), or `None` if no
            links are found.
        """
        links = await self._link_store.get_links_for_sdm_table(
            schema_name=schema_name, table_name=table_name
        )
        if len(links) == 0:
            return None
        return links

    async def get_links_for_sdm_column(
        self, *, schema_name: str, table_name: str, column_name: str
    ) -> list[SdmColumnLink] | None:
        """Get links for documentation on an SDM column.

        Parameters
        ----------
        schema_name
            The name of the schema.
        table_name
            The name of the table.
        column_name
            The name of the column.

        Returns
        -------
        list | None
            A list of links about the SDM (`SdmColumnLink`), or `None` if no
            links are found.
        """
        links = await self._link_store.get_links_for_sdm_column(
            column_name=column_name,
            schema_name=schema_name,
            table_name=table_name,
        )
        if len(links) == 0:
            return None
        return links

    async def get_links_for_sdm_columns_in_table(
        self,
        *,
        schema_name: str,
        table_name: str,
        limit: int | None = None,
        cursor: SdmColumnLinksCollectionCursor | None = None,
    ) -> CountedPaginatedList[
        SdmColumnLinksCollection, SdmColumnLinksCollectionCursor
    ]:
        """Get links for documentation on all columns scoped to an SDM table.

        Parameters
        ----------
        schema_name
            The name of the schema.
        table_name
            The name of the table.
        limit
            The maximum number of links to return in each page. Set to `None`
            to return all links.
        cursor
            A cursor for pagination. Set to `None` to start from the first
            page.

        Returns
        -------
        CountedPaginatedList
            A paginated list of links about the SDM columns
            (`SdmColumnLinksCollection`).
        """
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
        """Get links for documentation on all tables and columns scoped to an
        SDM schema.

        Parameters
        ----------
        schema_name
            The name of the schema.
        limit
            The maximum number of links to return in each page. Set to `None`
            to return all links.
        cursor
            A cursor for pagination. Set to `None` to start from the first
            page.

        Returns
        -------
        CountedPaginatedList
            A paginated list of links about the SDM tables
            (`SdmTableLinksCollection`).
        """
        return await self._link_store.get_table_links_for_sdm_schema(
            schema_name=schema_name,
            limit=limit,
            cursor=cursor,
        )

    async def get_links_for_sdm_collection(
        self,
        *,
        include_schemas: bool,
        include_tables: bool,
        include_columns: bool,
        schema_name: str | None = None,
        limit: int | None = None,
        cursor: SdmLinksCollectionCursor | None = None,
    ) -> CountedPaginatedList[SdmLinksCollection, SdmLinksCollectionCursor]:
        """Get links for documentation on schemas, tables, and columns in the
        SDM domain.

        Parameters
        ----------
        include_schemas
            Whether to include links for schemas.
        include_tables
            Whether to include links for tables.
        include_columns
            Whether to include links for columns.
        schema_name
            The name of the schema to filter by. Set to `None` to include
            all schemas.
        limit
            The maximum number of SDM entities to return in each page. Set to
            `None` to return all links.
        cursor
            A cursor for pagination. Set to `None` to start from the first
            page.

        Returns
        -------
        CountedPaginatedList
            A paginated list of links about the SDM entities
            (`SdmLinksCollection`).
        """
        return await self._link_store.get_sdm_links(
            include_schemas=include_schemas,
            include_tables=include_tables,
            include_columns=include_columns,
            schema_name=schema_name,
            limit=limit,
            cursor=cursor,
        )
