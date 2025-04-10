"""Interface to SQL tables for documentation links."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Self, override

from safir.database import (
    CountedPaginatedList,
    CountedPaginatedQueryRunner,
    InvalidCursorError,
    PaginationCursor,
)
from safir.datetime import current_datetime
from sqlalchemy import (
    CTE,
    BigInteger,
    Select,
    and_,
    cast,
    func,
    literal,
    literal_column,
    or_,
    select,
    union_all,
)
from sqlalchemy.ext.asyncio import async_scoped_session
from sqlalchemy.sql import text
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
    SdmLinksCollection,
    SdmSchemaLink,
    SdmTableLink,
)

__all__ = ["LinkStore", "SdmColumnLinksCollectionCursor", "SdmSchemaBulkLinks"]


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
        stmt = (
            select(
                SqlSdmSchema.name.label("schema_name"),
                SqlLink.html_url,
                SqlLink.source_type.label("type"),
                SqlLink.source_title.label("title"),
                SqlLink.source_collection_title.label("collection_title"),
            )
            .select_from(SqlSdmSchemaLink)
            .join(SqlSdmSchema)
            .where(SqlSdmSchema.name == schema_name)
        )
        results = (await self._session.execute(stmt)).fetchall()

        return [
            SdmSchemaLink.model_validate(
                result,
                from_attributes=True,
            )
            for result in results
        ]

    async def get_links_for_sdm_table(
        self, schema_name: str, table_name: str
    ) -> list[SdmTableLink]:
        """Get links for an SDM Table."""
        stmt = (
            select(
                SqlSdmTable.name.label("table_name"),
                SqlSdmSchema.name.label("schema_name"),
                SqlLink.html_url,
                SqlLink.source_type.label("type"),
                SqlLink.source_title.label("title"),
                SqlLink.source_collection_title.label("collection_title"),
            )
            .select_from(SqlSdmTableLink)
            .join(SqlSdmTable, SqlSdmTable.id == SqlSdmTableLink.table_id)
            .join(SqlSdmSchema, SqlSdmSchema.id == SqlSdmTable.schema_id)
            .where(
                SqlSdmSchema.name == schema_name,
                SqlSdmTable.name == table_name,
            )
        )
        results = (await self._session.execute(stmt)).fetchall()

        return [
            SdmTableLink.model_validate(
                result,
                from_attributes=True,
            )
            for result in results
        ]

    async def get_links_for_sdm_column(
        self, schema_name: str, table_name: str, column_name: str
    ) -> list[SdmColumnLink]:
        """Get links for an SDM Column."""
        stmt = (
            select(
                SqlSdmColumn.name.label("column_name"),
                SqlSdmTable.name.label("table_name"),
                SqlSdmSchema.name.label("schema_name"),
                SqlLink.html_url,
                SqlLink.source_type.label("type"),
                SqlLink.source_title.label("title"),
                SqlLink.source_collection_title.label("collection_title"),
            )
            .select_from(SqlSdmColumnLink)
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
        results = (
            await self._session.execute(
                stmt,
            )
        ).fetchall()

        return [
            SdmColumnLink.model_validate(
                result,
                from_attributes=True,
            )
            for result in results
        ]

    async def get_column_links_for_sdm_table(
        self,
        schema_name: str,
        table_name: str,
        *,
        limit: int | None = None,
        cursor: SdmColumnLinksCollectionCursor | None = None,
    ) -> CountedPaginatedList[
        SdmColumnLinksCollection, SdmColumnLinksCollectionCursor
    ]:
        """Get links for all columns in an SDM table (paginated).

        Optionally bypass pagination by setting ``limit`` to None and omitting
        the ``cursor``.
        """
        # The select matches the shape of the SdmColumnLinksCollection model.
        # Additionally the pagination cursor extracts the column_name and
        # tap_column_index.
        stmt = (
            select(
                literal("sdm").label("domain"),
                literal("column").label("entity_type"),
                SqlSdmColumn.name.label("column_name"),
                SqlSdmTable.name.label("table_name"),
                SqlSdmSchema.name.label("schema_name"),
                SqlSdmColumn.tap_column_index,
                func.json_agg(
                    func.json_build_object(
                        "column_name",
                        SqlSdmColumn.name,
                        "table_name",
                        SqlSdmTable.name,
                        "schema_name",
                        SqlSdmSchema.name,
                        "html_url",
                        SqlLink.html_url,
                        "type",
                        SqlLink.source_type,
                        "title",
                        SqlLink.source_title,
                        "collection_title",
                        SqlLink.source_collection_title,
                    )
                ).label("links"),
            )
            # Providing a starting point for the query
            .select_from(SqlSdmColumnLink)
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
            # Explicit grouping to help with the aggregation
            .group_by(
                SqlSdmColumn.tap_column_index,
                SqlSdmColumn.name,
                SqlSdmTable.name,
                SqlSdmSchema.name,
            )
        )

        runner = CountedPaginatedQueryRunner(
            entry_type=SdmColumnLinksCollection,
            cursor_type=SdmColumnLinksCollectionCursor,
        )
        return await runner.query_row(
            session=self._session,
            stmt=stmt,
            cursor=cursor,
            limit=limit,
        )

    async def get_sdm_links_scoped_to_schema(
        self, *, schema_name: str, include_columns: bool
    ) -> list[SdmLinksCollection]:
        """Get links to entities scoped to an SDM schema."""
        # This method can potentially return both table and column links
        # if include_columns is True. To handle this, we will create two
        # Common Table Expressions (CTEs) for table and column links, and
        # then combine them using a UNION ALL.

        table_links_cte = self._create_table_links_cte(schema_name=schema_name)
        column_links_cte = self._create_column_links_cte(
            schema_name=schema_name
        )

        stmt = select(
            literal_column("combined_links.domain").label("domain"),
            literal_column("combined_links.entity_type").label("entity_type"),
            literal_column("combined_links.schema_name").label("schema_name"),
            literal_column("combined_links.table_name").label("table_name"),
            literal_column("combined_links.column_name").label("column_name"),
            literal_column("combined_links.tap_table_index").label(
                "tap_table_index"
            ),
            literal_column("combined_links.tap_column_index").label(
                "tap_column_index"
            ),
            func.json_agg(
                func.json_build_object(
                    "schema_name",
                    text("schema_name"),
                    "table_name",
                    text("table_name"),
                    "column_name",
                    text("column_name"),
                    "html_url",
                    text("html_url"),
                    "type",
                    text("source_type"),
                    "title",
                    text("source_title"),
                    "collection_title",
                    text("source_collection_title"),
                )
            ).label("links"),
        )
        if include_columns:
            stmt = stmt.select_from(
                union_all(
                    select(table_links_cte),
                    select(column_links_cte),
                ).alias("combined_links")
            )
        else:
            stmt = stmt.select_from(table_links_cte.alias("combined_links"))

        stmt = stmt.group_by(
            text("domain"),
            text("entity_type"),
            text("schema_name"),
            text("table_name"),
            text("column_name"),
            text("tap_table_index"),
            text("tap_column_index"),
        ).order_by(
            text("domain"),
            text("entity_type"),
            text("tap_table_index"),
            text("tap_column_index"),
            text("table_name"),
            text("column_name"),
        )

        results = (await self._session.execute(stmt)).fetchall()
        if not results:
            return []

        return [
            SdmLinksCollection.model_validate(result, from_attributes=True)
            for result in results
        ]

    async def get_sdm_links(
        self, *, include_tables: bool, include_columns: bool
    ) -> list[SdmLinksCollection]:
        """Get links for SDM schema entities and optionally their members."""
        # This method can potentially return schema, table, and column links
        # if include_tables and include_columns are True.

        table_links_cte = self._create_table_links_cte()
        column_links_cte = self._create_column_links_cte()
        schema_links_cte = self._create_schema_links_cte()

        stmt = select(
            literal_column("combined_links.domain").label("domain"),
            literal_column("combined_links.entity_type").label("entity_type"),
            literal_column("combined_links.schema_name").label("schema_name"),
            literal_column("combined_links.table_name").label("table_name"),
            literal_column("combined_links.column_name").label("column_name"),
            literal_column("combined_links.tap_table_index").label(
                "tap_table_index"
            ),
            literal_column("combined_links.tap_column_index").label(
                "tap_column_index"
            ),
            func.json_agg(
                func.json_build_object(
                    "schema_name",
                    text("schema_name"),
                    "table_name",
                    text("table_name"),
                    "column_name",
                    text("column_name"),
                    "html_url",
                    text("html_url"),
                    "type",
                    text("source_type"),
                    "title",
                    text("source_title"),
                    "collection_title",
                    text("source_collection_title"),
                )
            ).label("links"),
        )
        if include_columns and include_tables:
            stmt = stmt.select_from(
                union_all(
                    select(schema_links_cte),
                    select(table_links_cte),
                    select(column_links_cte),
                ).alias("combined_links")
            )
        elif include_tables is True and include_columns is False:
            stmt = stmt.select_from(
                union_all(
                    select(schema_links_cte),
                    select(table_links_cte),
                ).alias("combined_links")
            )
        elif include_tables is False and include_columns is True:
            stmt = stmt.select_from(
                union_all(
                    select(schema_links_cte),
                    select(column_links_cte),
                ).alias("combined_links")
            )
        else:
            stmt = stmt.select_from(schema_links_cte.alias("combined_links"))

        stmt = stmt.group_by(
            text("domain"),
            text("schema_name"),
            text("entity_type"),
            text("table_name"),
            text("column_name"),
            text("tap_table_index"),
            text("tap_column_index"),
        ).order_by(
            text("domain"),
            text("schema_name"),
            text("entity_type"),
            text("tap_table_index"),
            text("tap_column_index"),
            text("table_name"),
            text("column_name"),
        )

        results = (await self._session.execute(stmt)).fetchall()
        if not results:
            return []

        return [
            SdmLinksCollection.model_validate(result, from_attributes=True)
            for result in results
        ]

    def _create_column_links_cte(self, schema_name: str | None = None) -> CTE:
        """Create a Common Table Expression (CTE) for column links.

        The CTE is designed to select columns necessary to build a
        SdmColumnLinksCollection or a SdmLinksCollection generally.

        Parameters
        ----------
        schema_name
            The name of the schema to filter by. If None, the `where` clause
            for the schema is omitted.
        """
        stmt = (
            select(
                literal("sdm").label("domain"),
                literal("column").label("entity_type"),
                SqlSdmSchema.name.label("schema_name"),
                SqlSdmTable.name.label("table_name"),
                SqlSdmColumn.name.label("column_name"),
                SqlLink.html_url,
                SqlLink.source_type,
                SqlLink.source_title,
                SqlLink.source_collection_title,
                cast(literal(None), BigInteger).label("tap_table_index"),
                SqlSdmColumn.tap_column_index,
            )
            .select_from(SqlSdmColumnLink)
            .join(SqlSdmColumn, SqlSdmColumn.id == SqlSdmColumnLink.column_id)
            .join(SqlSdmTable, SqlSdmTable.id == SqlSdmColumn.table_id)
            .join(SqlSdmSchema, SqlSdmSchema.id == SqlSdmTable.schema_id)
        )

        if schema_name:
            stmt = stmt.where(SqlSdmSchema.name == schema_name)

        return stmt.cte("column_links")

    def _create_table_links_cte(self, schema_name: str | None = None) -> CTE:
        """Create a Common Table Expression (CTE) for table links.

        The CTE is designed to select columns necessary to build a
        SdmTableLinksCollection or a SdmLinksCollection generally.

        Parameters
        ----------
        schema_name
            The name of the schema to filter by. If None, the `where` clause
            for the schema is omitted.
        """
        stmt = (
            select(
                literal("sdm").label("domain"),
                literal("table").label("entity_type"),
                SqlSdmSchema.name.label("schema_name"),
                SqlSdmTable.name.label("table_name"),
                literal(None).label("column_name"),
                SqlLink.html_url,
                SqlLink.source_type,
                SqlLink.source_title,
                SqlLink.source_collection_title,
                SqlSdmTable.tap_table_index,
                cast(literal(None), BigInteger).label("tap_column_index"),
            )
            .select_from(SqlSdmTableLink)
            .join(SqlSdmTable, SqlSdmTable.id == SqlSdmTableLink.table_id)
            .join(SqlSdmSchema, SqlSdmSchema.id == SqlSdmTable.schema_id)
        )

        if schema_name:
            stmt = stmt.where(SqlSdmSchema.name == schema_name)

        return stmt.cte("table_links")

    def _create_schema_links_cte(self) -> CTE:
        """Create a Common Table Expression (CTE) for schema links.

        The CTE is designed to select columns necessary to build a
        SdmSchemaLinksCollection or a SdmLinksCollection generally.
        """
        stmt = (
            select(
                literal("sdm").label("domain"),
                literal("schema").label("entity_type"),
                SqlSdmSchema.name.label("schema_name"),
                literal(None).label("table_name"),
                literal(None).label("column_name"),
                SqlLink.html_url,
                SqlLink.source_type,
                SqlLink.source_title,
                SqlLink.source_collection_title,
                cast(literal(None), BigInteger).label("tap_table_index"),
                cast(literal(None), BigInteger).label("tap_column_index"),
            )
            .select_from(SqlSdmSchemaLink)
            .join(SqlSdmSchema, SqlSdmSchema.id == SqlSdmSchemaLink.schema_id)
        )

        return stmt.cte("schema_links")

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
                    SqlSdmSchema.name == schema_links.schema.schema_name
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
                        SqlSdmTable.name == table_link.table_name,
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
                        SqlSdmColumn.name == column_link.column_name,
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


@dataclass
class SdmColumnLinksCollectionCursor(
    PaginationCursor[SdmColumnLinksCollection]
):
    """A pagination cursor for SdmColumnLinksCollection results.

    The cursor involves the tap_column_index and column_name of the SDM
    column. Our compound sorting order of columns is:

    1. tap_column_index in increasing order, treating NULL as infinitely
       large.
    2. column_name in increasing alphabetic order for a given tap_column_index.
    """

    __slots__ = ("column_name", "previous", "tap_column_index")

    tap_column_index: int | None

    column_name: str

    @override
    @classmethod
    def from_entry(
        cls, entry: SdmColumnLinksCollection, *, reverse: bool = False
    ) -> Self:
        """Construct a cursor with an entry as the bound."""
        # The cursor is a tuple of the schema name, table name, and column name
        # in natural order.
        return cls(
            tap_column_index=entry.tap_column_index,
            column_name=entry.column_name,
            previous=reverse,
        )

    @override
    @classmethod
    def from_str(cls, cursor: str) -> Self:
        """Build cursor from the string serialization form."""
        # decode base64
        try:
            decoded = base64.b64decode(cursor).decode("utf-8")
            data = json.loads(decoded)
            return cls(
                tap_column_index=data["tap_column_index"],
                column_name=data["column_name"],
                previous=data["previous"],
            )
        except Exception as e:
            raise InvalidCursorError(f"Cannot parse cursor: {e!s}") from e

    @override
    @classmethod
    def apply_order(cls, stmt: Select, *, reverse: bool = False) -> Select:
        """Apply the sort order of the cursor to a select statement."""
        if reverse:
            # For reverse order, NULL comes first since it's infinitely large
            stmt = stmt.order_by(
                SqlSdmColumn.tap_column_index.desc().nulls_first(),
                SqlSdmColumn.name.desc(),
            )
        else:
            # For forward order, NULL comes last since it's infinitely large
            stmt = stmt.order_by(
                SqlSdmColumn.tap_column_index.asc().nulls_last(),
                SqlSdmColumn.name,
            )
        return stmt

    @override
    def apply_cursor(self, stmt: Select) -> Select:
        """Apply the restrictions from the cursor to a select statement."""
        # Specially handle NULL in tap_column_index because we treat it as
        # infinitely large for ordering purposes.
        if self.tap_column_index is None:
            # Cursor is at a NULL position
            if self.previous:
                # When moving backwards from a NULL position, get all non-NULL
                # rows or rows with NULL tap_column_index but smaller column
                # name
                stmt = stmt.where(
                    or_(
                        SqlSdmColumn.tap_column_index.is_not(None),
                        and_(
                            SqlSdmColumn.tap_column_index.is_(None),
                            SqlSdmColumn.name < self.column_name,
                        ),
                    )
                )
            else:
                # When moving forwards from a NULL position, only get rows with
                # NULL tap_column_index and larger column name
                stmt = stmt.where(
                    and_(
                        SqlSdmColumn.tap_column_index.is_(None),
                        SqlSdmColumn.name > self.column_name,
                    )
                )
        # Handle cases where tap_column_index is not NULL
        elif self.previous:
            # Going backwards
            stmt = stmt.where(
                or_(
                    # Smaller index values
                    SqlSdmColumn.tap_column_index < self.tap_column_index,
                    # Same index value, smaller column name
                    and_(
                        SqlSdmColumn.tap_column_index == self.tap_column_index,
                        SqlSdmColumn.name < self.column_name,
                    ),
                )
            )
        else:
            # Going forwards
            stmt = stmt.where(
                or_(
                    # NULL values (they're largest)
                    SqlSdmColumn.tap_column_index.is_(None),
                    # Larger index values
                    SqlSdmColumn.tap_column_index > self.tap_column_index,
                    # Same index value, larger column name
                    and_(
                        SqlSdmColumn.tap_column_index == self.tap_column_index,
                        SqlSdmColumn.name > self.column_name,
                    ),
                )
            )
        return stmt

    @override
    def invert(self) -> Self:
        return type(self)(
            tap_column_index=self.tap_column_index,
            column_name=self.column_name,
            previous=not self.previous,
        )

    def __str__(self) -> str:
        """Serialize to string.

        This is the reverse of from_str.
        """
        data = {
            "tap_column_index": self.tap_column_index,
            "column_name": self.column_name,
            "previous": self.previous,
        }
        # encode base64
        encoded = base64.b64encode(json.dumps(data).encode("utf-8"))
        return encoded.decode("utf-8")
