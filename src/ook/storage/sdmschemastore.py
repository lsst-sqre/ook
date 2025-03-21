"""Interface to SQL tables for SDM Schemas links."""

from __future__ import annotations

from datetime import timedelta

from safir.datetime import current_datetime
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_scoped_session
from structlog.stdlib import BoundLogger

from ook.dbschema.sdmschemas import SqlSdmColumn, SqlSdmSchema, SqlSdmTable
from ook.domain.sdmschemas import (
    SdmColumn,
    SdmSchema,
    SdmSchemaRecursive,
    SdmTableRecursive,
)

__all__ = ["SdmSchemasStore"]


class SdmSchemasStore:
    """An interface to the SQL tables for SDM Schemas."""

    def __init__(
        self, session: async_scoped_session, logger: BoundLogger
    ) -> None:
        self._session = session
        self._logger = logger

    async def update_schema(self, schema: SdmSchemaRecursive) -> None:
        """Upsert a schema, including its tables and columns."""
        now = current_datetime(microseconds=False)

        # Prepare the schema data
        schema_data = {
            "name": schema.name,
            "felis_id": schema.felis_id,
            "description": schema.description,
            "github_owner": schema.github_owner,
            "github_repo": schema.github_repo,
            "github_ref": schema.github_ref,
            "github_path": schema.github_path,
            "date_updated": now,
        }

        # Upsert schema and get its ID
        result = await self._session.execute(
            pg_insert(SqlSdmSchema)
            .values([schema_data])
            .on_conflict_do_update(index_elements=["name"], set_=schema_data)
            .returning(SqlSdmSchema.id)
        )
        schema_id = result.scalar_one()
        await self._session.flush()
        self._logger.debug(
            "Upserted schema", schema_id=schema_id, name=schema.name
        )

        # Prepare table data
        table_data = [
            {
                "schema_id": schema_id,
                "name": table.name,
                "felis_id": table.felis_id,
                "description": table.description,
                "tap_table_index": table.tap_table_index,
                "date_updated": now,
            }
            for table in schema.tables
        ]

        # Bulk upsert tables
        table_insert_stmt = pg_insert(SqlSdmTable).values(table_data)
        table_stmt = table_insert_stmt.on_conflict_do_update(
            index_elements=["schema_id", "name"],
            set_={
                "felis_id": table_insert_stmt.excluded.felis_id,
                "description": table_insert_stmt.excluded.description,
                "tap_table_index": (
                    table_insert_stmt.excluded.tap_table_index
                ),
                "date_updated": table_insert_stmt.excluded.date_updated,
            },
        ).returning(SqlSdmTable.id, SqlSdmTable.name)
        result = await self._session.execute(table_stmt)
        table_id_map = {name: id_ for id_, name in result.fetchall()}
        await self._session.flush()
        self._logger.debug("Upserted tables", table_id_map=table_id_map)

        # Prepare column data
        column_data = []
        for table in schema.tables:
            for column in table.columns:
                table_id = table_id_map.get(table.name)
                if table_id is None:
                    # Shouldn't happen because table_id_map shuld have all
                    # tables even if they weren't updated.
                    continue
                column_data.append(
                    {
                        "table_id": table_id,
                        "name": column.name,
                        "felis_id": column.felis_id,
                        "description": column.description,
                        "datatype": str(column.datatype),
                        "ivoa_ucd": column.ivoa_ucd,
                        "ivoa_unit": column.ivoa_unit,
                        "tap_column_index": column.tap_column_index,
                        "date_updated": now,
                    }
                )

        # Bulk upsert columns
        if column_data:
            self._logger.debug(
                "Upserting columns",
                column_count=len(column_data),
                sample_column=column_data[0] if column_data else None,
                table_ids=list({c["table_id"] for c in column_data}),
            )
            column_stmt = pg_insert(SqlSdmColumn).values(column_data)
            column_stmt = column_stmt.on_conflict_do_update(
                index_elements=["table_id", "name"],
                set_={
                    "felis_id": column_stmt.excluded.felis_id,
                    "description": column_stmt.excluded.description,
                    "datatype": column_stmt.excluded.datatype,
                    "ivoa_ucd": column_stmt.excluded.ivoa_ucd,
                    "ivoa_unit": column_stmt.excluded.ivoa_unit,
                    "tap_column_index": (
                        column_stmt.excluded.tap_column_index
                    ),
                    "date_updated": column_stmt.excluded.date_updated,
                },
            )
            await self._session.execute(column_stmt)
        await self._session.flush()
        self._logger.debug("Upserted columns", schema_name=schema.name)

        # Delete old records for this schema that weren't updated
        second_ago = now - timedelta(seconds=2)

        sub_query = (
            select(SqlSdmColumn.id)
            .join(SqlSdmTable)
            .join(SqlSdmSchema)
            .where(SqlSdmSchema.name == schema.name)
            .where(SqlSdmColumn.date_updated < second_ago)
            .scalar_subquery()
        )
        result = await self._session.execute(
            delete(SqlSdmColumn).where(SqlSdmColumn.id.in_(sub_query))
        )
        await self._session.flush()
        self._logger.debug(
            "Checked outdated columns",
            schema_name=schema.name,
            deleted_columns=result.rowcount,
        )

        sub_query = (
            select(SqlSdmTable.id)
            .join(SqlSdmSchema)
            .where(SqlSdmSchema.name == schema.name)
            .where(SqlSdmTable.date_updated < second_ago)
            .scalar_subquery()
        )
        result = await self._session.execute(
            delete(SqlSdmTable).where(SqlSdmTable.id.in_(sub_query))
        )
        await self._session.flush()
        self._logger.debug(
            "Checked outdated tables",
            schema_name=schema.name,
            deleted_tables=result.rowcount,
        )

        sub_query = (
            select(SqlSdmSchema.id)
            .where(SqlSdmSchema.name == schema.name)
            .where(SqlSdmSchema.date_updated < second_ago)
            .scalar_subquery()
        )
        result = await self._session.execute(
            delete(SqlSdmSchema).where(SqlSdmSchema.id.in_(sub_query))
        )
        await self._session.flush()
        self._logger.debug(
            "Checked outdated schemas",
            schema_name=schema.name,
            deleted_schemas=result.rowcount,
        )

    async def get_schema(self, schema_name: str) -> SdmSchema | None:
        """Get a schema link by name."""
        result = await self._session.execute(
            SqlSdmSchema.__table__.select().where(
                SqlSdmSchema.name == schema_name
            )
        )
        row = result.fetchone()
        if row is None:
            return None
        return SdmSchema(
            name=row.name,
            felis_id=row.felis_id,
            description=row.description,
            github_owner=row.github_owner,
            github_repo=row.github_repo,
            github_ref=row.github_ref,
            github_path=row.github_path,
        )

    async def list_schemas(self) -> list[SdmSchema]:
        """List all schemas."""
        result = await self._session.execute(
            SqlSdmSchema.__table__.select().order_by(SqlSdmSchema.name)
        )
        return [
            SdmSchema(
                name=row.name,
                felis_id=row.felis_id,
                description=row.description,
                github_owner=row.github_owner,
                github_repo=row.github_repo,
                github_ref=row.github_ref,
                github_path=row.github_path,
            )
            for row in result.fetchall()
        ]

    async def get_schema_by_repo_path(
        self,
        *,
        github_owner: str,
        github_repo: str,
        github_path: str,
    ) -> SdmSchemaRecursive | None:
        """Get the SDM Schema corresponding to a specifiic path for its YAML
        file in a GitHub repository.

        This is used when ingesting links to the sdm-schemas.lsst.io schema
        browser because those Markdown files reference schemas by YAML
        filename rather than schema name or ID.

        Parameters
        ----------
        github_owner
            Owner of the GitHub repository hosting the schema YAML.
        github_repo
            Name of the GitHub repository hosting the schema YAML.
        github_path
            Path to the schema YAML file in the repository.
        """
        result = await self._session.execute(
            SqlSdmSchema.__table__.select()
            .where(SqlSdmSchema.github_owner == github_owner)
            .where(SqlSdmSchema.github_repo == github_repo)
            .where(SqlSdmSchema.github_path == github_path)
        )
        row = result.fetchone()
        if row is None:
            return None
        sdm_schema = SdmSchemaRecursive(
            name=row.name,
            felis_id=row.felis_id,
            description=row.description,
            github_owner=row.github_owner,
            github_repo=row.github_repo,
            github_ref=row.github_ref,
            github_path=row.github_path,
            tables=[],
        )

        # Query for tables belonging to the schema
        table_result = await self._session.execute(
            SqlSdmTable.__table__.select().where(
                SqlSdmTable.schema_id == row.id
            )
        )
        for table_row in table_result.fetchall():
            # Query for columns belonging to the table
            column_result = await self._session.execute(
                SqlSdmColumn.__table__.select().where(
                    SqlSdmColumn.table_id == table_row.id
                )
            )
            columns = [
                SdmColumn(
                    name=column_row.name,
                    felis_id=column_row.felis_id,
                    table_name=table_row.name,
                    schema_name=row.name,
                    description=column_row.description,
                    datatype=column_row.datatype,
                    ivoa_ucd=column_row.ivoa_ucd,
                    ivoa_unit=column_row.ivoa_unit,
                    tap_column_index=column_row.tap_column_index,
                )
                for column_row in column_result.fetchall()
            ]
            sdm_schema.tables.append(
                SdmTableRecursive(
                    name=table_row.name,
                    felis_id=table_row.felis_id,
                    schema_name=row.name,
                    description=table_row.description,
                    tap_table_index=table_row.tap_table_index,
                    columns=columns,
                )
            )

        return sdm_schema
