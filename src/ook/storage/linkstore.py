"""Interface to SQL tables for documentation links."""

from __future__ import annotations

from collections import defaultdict

from safir.datetime import current_datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_scoped_session
from structlog.stdlib import BoundLogger

from ook.dbschema.base import Base
from ook.dbschema.links import (
    SqlLink,
    SqlSdmColumnLink,
    SqlSdmSchemaLink,
    SqlSdmTableLink,
)
from ook.dbschema.sdmschemas import SqlSdmColumn, SqlSdmSchema, SqlSdmTable
from ook.domain.links import (
    SdmColumnLink,
    SdmColumnLinksCollection,
    SdmSchemaLink,
    SdmTableLink,
)

__all__ = ["LinkStore", "SdmSchemaBulkLinks"]


class LinkStore:
    """An interface to documentation links in a database."""

    def __init__(
        self, session: async_scoped_session, logger: BoundLogger
    ) -> None:
        self._session = session
        self._logger = logger

    async def get_links_for_sdm_schema(
        self, schema_name: str
    ) -> list[SdmSchemaLink]:
        """Get links for an SDM Schema."""
        results = (
            await self._session.execute(
                select(SqlSdmSchemaLink, SqlLink)
                .join(SqlSdmSchema)
                .where(SqlSdmSchema.name == schema_name)
            )
        ).fetchall()

        return [
            SdmSchemaLink(
                name=schema_name,
                html_url=result.SqlLink.html_url,
                type=result.SqlLink.source_type,
                title=result.SqlLink.source_title,
                collection_title=result.SqlLink.source_collection_title,
            )
            for result in results
        ]

    async def get_links_for_sdm_table(
        self, schema_name: str, table_name: str
    ) -> list[SdmTableLink]:
        """Get links for an SDM Table."""
        results = (
            await self._session.execute(
                select(SqlSdmTableLink, SqlLink)
                .join(SqlSdmTable, SqlSdmTable.id == SqlSdmTableLink.table_id)
                .join(SqlSdmSchema, SqlSdmSchema.id == SqlSdmTable.schema_id)
                .where(
                    SqlSdmSchema.name == schema_name,
                    SqlSdmTable.name == table_name,
                )
            )
        ).fetchall()

        return [
            SdmTableLink(
                name=table_name,
                schema_name=schema_name,
                html_url=result.SqlLink.html_url,
                type=result.SqlLink.source_type,
                title=result.SqlLink.source_title,
                collection_title=result.SqlLink.source_collection_title,
            )
            for result in results
        ]

    async def get_links_for_sdm_column(
        self, schema_name: str, table_name: str, column_name: str
    ) -> list[SdmColumnLink]:
        """Get links for an SDM Column."""
        results = (
            await self._session.execute(
                select(SqlSdmColumnLink, SqlLink)
                .join(
                    SqlSdmColumn,
                    SqlSdmColumn.id == SqlSdmColumnLink.column_id,
                )
                .join(SqlSdmTable, SqlSdmTable.id == SqlSdmColumn.table_id)
                .join(SqlSdmSchema, SqlSdmSchema.id == SqlSdmTable.schema_id)
                .where(
                    SqlSdmSchema.name == schema_name,
                    SqlSdmTable.name == table_name,
                    SqlSdmColumn.name == column_name,
                )
            )
        ).fetchall()

        return [
            SdmColumnLink(
                name=column_name,
                table_name=table_name,
                schema_name=schema_name,
                html_url=result.SqlLink.html_url,
                type=result.SqlLink.source_type,
                title=result.SqlLink.source_title,
                collection_title=result.SqlLink.source_collection_title,
            )
            for result in results
        ]

    async def get_column_links_for_sdm_table(
        self, schema_name: str, table_name: str
    ) -> list[SdmColumnLinksCollection]:
        """Get links for all columns in an SDM table."""
        # Links for all columns in a table
        results = (
            await self._session.execute(
                select(SqlSdmColumnLink, SqlLink, SqlSdmColumn)
                .join(
                    SqlSdmColumn,
                    SqlSdmColumn.id == SqlSdmColumnLink.column_id,
                )
                .join(SqlSdmTable, SqlSdmTable.id == SqlSdmColumn.table_id)
                .join(SqlSdmSchema, SqlSdmSchema.id == SqlSdmTable.schema_id)
                .where(
                    SqlSdmSchema.name == schema_name,
                    SqlSdmTable.name == table_name,
                )
            )
        ).fetchall()

        # Columns in a table
        columns = (
            await self._session.execute(
                select(SqlSdmColumn.name)
                .join(SqlSdmTable)
                .join(SqlSdmSchema)
                .where(
                    SqlSdmSchema.name == schema_name,
                    SqlSdmTable.name == table_name,
                )
                .order_by(SqlSdmColumn.tap_column_index, SqlSdmColumn.name)
            )
        ).fetchall()

        # Group links by column
        links_by_column: dict[str, list[SdmColumnLink]] = defaultdict(list)
        for result in results:
            column_name = result.SqlSdmColumn.name
            link = SdmColumnLink(
                name=column_name,
                table_name=table_name,
                schema_name=schema_name,
                html_url=result.SqlLink.html_url,
                type=result.SqlLink.source_type,
                title=result.SqlLink.source_title,
                collection_title=result.SqlLink.source_collection_title,
            )
            links_by_column[column_name].append(link)

        # Create collections for all columns, even those without links
        return [
            SdmColumnLinksCollection(
                schema_name=schema_name,
                table_name=table_name,
                column_name=column.name,
                links=links_by_column.get(column.name, []),
            )
            for column in columns
        ]

    async def update_sdm_schema_links(
        self, schema_links: SdmSchemaBulkLinks
    ) -> None:
        """Update link records for a schema pointing to sdm-schemas.lsst.io,
        including the schema itself, tables, and columns using bulk upsert
        operations.
        """
        now = current_datetime(microseconds=False)

        # Get the schema record
        schema = (
            await self._session.execute(
                select(SqlSdmSchema).where(
                    SqlSdmSchema.name == schema_links.schema.name
                )
            )
        ).scalar_one()

        # Upsert schema link
        await self._upsert_joined_inheritance(
            child_model=SqlSdmSchemaLink,
            parent_model=SqlLink,
            join_conditions={
                "schema_id": schema.id,
                "html_url": schema_links.schema.html_url,
            },
            update_fields={
                "source_type": schema_links.schema.type,
                "source_title": schema_links.schema.title,
                "source_collection_title": (
                    schema_links.schema.collection_title
                ),
                "date_updated": now,
            },
            insert_fields={
                "html_url": schema_links.schema.html_url,
                "source_type": schema_links.schema.type,
                "source_title": schema_links.schema.title,
                "source_collection_title": (
                    schema_links.schema.collection_title
                ),
                "date_updated": now,
                "schema_id": schema.id,
            },
        )

        # Get table records and upsert table links
        for table_link in schema_links.tables:
            table = (
                await self._session.execute(
                    select(SqlSdmTable).where(
                        SqlSdmTable.schema_id == schema.id,
                        SqlSdmTable.name == table_link.name,
                    )
                )
            ).scalar_one()

            await self._upsert_joined_inheritance(
                child_model=SqlSdmTableLink,
                parent_model=SqlLink,
                join_conditions={
                    "table_id": table.id,
                    "html_url": table_link.html_url,
                },
                update_fields={
                    "source_type": table_link.type,
                    "source_title": table_link.title,
                    "source_collection_title": table_link.collection_title,
                    "date_updated": now,
                },
                insert_fields={
                    "html_url": table_link.html_url,
                    "source_type": table_link.type,
                    "source_title": table_link.title,
                    "source_collection_title": table_link.collection_title,
                    "date_updated": now,
                    "table_id": table.id,
                },
            )

        # Get column records and upsert column links
        for column_link in schema_links.columns:
            column = (
                await self._session.execute(
                    select(SqlSdmColumn)
                    .join(SqlSdmTable)
                    .where(
                        SqlSdmTable.schema_id == schema.id,
                        SqlSdmTable.name == column_link.table_name,
                        SqlSdmColumn.name == column_link.name,
                    )
                )
            ).scalar_one()

            await self._upsert_joined_inheritance(
                child_model=SqlSdmColumnLink,
                parent_model=SqlLink,
                join_conditions={
                    "column_id": column.id,
                    "html_url": column_link.html_url,
                },
                update_fields={
                    "source_type": column_link.type,
                    "source_title": column_link.title,
                    "source_collection_title": column_link.collection_title,
                    "date_updated": now,
                },
                insert_fields={
                    "html_url": column_link.html_url,
                    "source_type": column_link.type,
                    "source_title": column_link.title,
                    "source_collection_title": column_link.collection_title,
                    "date_updated": now,
                    "column_id": column.id,
                },
            )

        await self._session.flush()

    async def _upsert_joined_inheritance[T](
        self,
        *,
        child_model: type[T],
        parent_model: type[Base],
        join_conditions: dict,
        update_fields: dict,
        insert_fields: dict,
    ) -> T:
        """Upsert a joined-table inheritance row by joining parent and child
        tables.
        """
        # SQLAlchemy automatically handles the joined table inheritance
        join_stmt = select(child_model)

        # Add where conditions
        for key, value in join_conditions.items():
            parent_col = getattr(parent_model, key, None)
            child_col = getattr(child_model, key, None)
            if parent_col is not None:
                join_stmt = join_stmt.where(parent_col == value)
            elif child_col is not None:
                join_stmt = join_stmt.where(child_col == value)
            else:
                raise ValueError(
                    f"Field {key} not found in parent or child model."
                )

        existing = (
            await self._session.execute(join_stmt)
        ).scalar_one_or_none()

        if existing:
            for field, value in update_fields.items():
                setattr(existing, field, value)
            instance = existing
        else:
            instance = child_model(**insert_fields)
            self._session.add(instance)

        await self._session.flush()
        return instance


class SdmSchemaBulkLinks:
    """A collection of links to schema documentation.

    This specialty strucutre is indended for bulk ingest of links for a schema.
    """

    def __init__(
        self,
        schema: SdmSchemaLink,
        tables: list[SdmTableLink],
        columns: list[SdmColumnLink],
    ) -> None:
        self.schema = schema
        self.tables = tables
        self.columns = columns
