"""Interface to SQL tables for SDM Schemas links."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_scoped_session
from structlog.stdlib import BoundLogger

from ook.dbschema.sdmschemaslinks import (
    SqlSdmColumnLink,
    SqlSdmSchemaLink,
    SqlSdmTableLink,
)

__all__ = [
    "SdmSchemasColumnLink",
    "SdmSchemasLinkStore",
    "SdmSchemasLinks",
    "SdmSchemasSchemaLink",
    "SdmSchemasTableLink",
]


class SdmSchemasLinkStore:
    """An interface to the SQL tables for SDM Schemas links."""

    def __init__(
        self, session: async_scoped_session, logger: BoundLogger
    ) -> None:
        self._session = session
        self._logger = logger

    async def update_schema(self, schema_links: SdmSchemasLinks) -> None:
        """Update link records for a schema, including the schema itself,
        tables, and columns using bulk upsert operations.
        """
        now = datetime.now(tz=UTC)

        # Prepare the schema data
        schema_data = {
            "name": schema_links.schema.name,
            "html_url": schema_links.schema.url,
            "date_updated": now,
        }

        # Upsert schema and get its ID
        result = await self._session.execute(
            pg_insert(SqlSdmSchemaLink)
            .values([schema_data])
            .on_conflict_do_update(index_elements=["name"], set_=schema_data)
            .returning(SqlSdmSchemaLink.id)
        )
        schema_id = result.scalar_one()

        # Prepare table data
        table_data = [
            {
                "schema_id": schema_id,
                "name": table.name,
                "html_url": table.url,
                "date_updated": now,
            }
            for table in schema_links.tables
        ]

        # Bulk upsert tables
        if table_data:
            table_insert_stmt = pg_insert(SqlSdmTableLink).values(table_data)
            table_stmt = table_insert_stmt.on_conflict_do_update(
                index_elements=["schema_id", "name"],
                set_={
                    "html_url": table_insert_stmt.excluded.html_url,
                    "date_updated": table_insert_stmt.excluded.date_updated,
                },
            ).returning(SqlSdmTableLink.id, SqlSdmTableLink.name)
            result = await self._session.execute(table_stmt)
            table_id_map = {name: id_ for id_, name in result.fetchall()}

        # Prepare column data
        column_data = []
        for column in schema_links.columns:
            table_id = table_id_map.get(column.table.name)
            if table_id:
                column_data.append(
                    {
                        "table_id": table_id,
                        "name": column.name,
                        "html_url": column.url,
                        "date_updated": now,
                    }
                )

        # Bulk upsert columns
        if column_data:
            column_stmt = pg_insert(SqlSdmColumnLink).values(column_data)
            column_stmt = column_stmt.on_conflict_do_update(
                index_elements=["table_id", "name"],
                set_={
                    "html_url": column_stmt.excluded.html_url,
                    "date_updated": column_stmt.excluded.date_updated,
                },
            )
            await self._session.execute(column_stmt)

        # Delete old records for this schema that weren't updated
        await self._session.execute(
            delete(SqlSdmColumnLink).where(
                SqlSdmColumnLink.table_id.in_(table_id_map.values()),
                SqlSdmColumnLink.date_updated < now,
            )
        )

        await self._session.execute(
            delete(SqlSdmTableLink).where(
                SqlSdmTableLink.schema_id == schema_id,
                SqlSdmTableLink.date_updated < now,
            )
        )

        await self._session.execute(
            delete(SqlSdmSchemaLink).where(
                SqlSdmSchemaLink.name == schema_links.schema.name,
                SqlSdmSchemaLink.date_updated < now,
            )
        )


@dataclass
class SdmSchemasLinks:
    """A collection of links for an SDM schema."""

    schema: SdmSchemasSchemaLink
    """The schema link."""

    tables: list[SdmSchemasTableLink]
    """The table links."""

    columns: list[SdmSchemasColumnLink]
    """The column links."""


@dataclass
class SdmSchemasSchemaLink:
    """A link to a schema on sdm-schemas.lsst.io."""

    name: str
    """The name of the database object."""

    id: str
    """The ID of the database object."""

    url: str
    """Documentation URL for the schema."""

    description: str | None
    """A description of the schema."""


@dataclass
class SdmSchemasTableLink:
    """A link to a table on sdm-schemas.lsst.io."""

    schema: SdmSchemasSchemaLink
    """The schema to which the table belongs."""

    name: str
    """The name of the database object."""

    id: str
    """The ID of the database object."""

    url: str
    """Documentation URL for the table."""

    description: str | None
    """A description of the table."""


@dataclass
class SdmSchemasColumnLink:
    """A link to a column on sdm-schemas.lsst.io."""

    table: SdmSchemasTableLink
    """The table to which the column belongs."""

    name: str
    """The name of the database object."""

    id: str
    """The ID of the database object."""

    url: str
    """Documentation URL for the column."""

    description: str | None
    """A description of the column."""
