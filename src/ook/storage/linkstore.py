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
from sqlalchemy.sql.elements import ColumnElement
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
    SdmSchemaLinksCollection,
    SdmTableLink,
    SdmTableLinksCollection,
)

__all__ = [
    "LinkStore",
    "SdmColumnLinksCollectionCursor",
    "SdmLinksCollectionCursor",
    "SdmSchemaBulkLinks",
    "SdmTableLinksCollectionCursor",
]


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
        stmt = self._create_sdm_link_collection_query(
            include_schemas=False, include_tables=False, include_columns=True
        ).where(
            and_(
                literal_column("combined_links.schema_name") == schema_name,
                literal_column("combined_links.table_name") == table_name,
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

    async def get_table_links_for_sdm_schema(
        self,
        schema_name: str,
        *,
        limit: int | None = None,
        cursor: SdmTableLinksCollectionCursor | None = None,
    ) -> CountedPaginatedList[
        SdmTableLinksCollection, SdmTableLinksCollectionCursor
    ]:
        """Get links for all tables in an SDM schema (paginated).

        Optionally bypass pagination by setting ``limit`` to None and omitting
        the ``cursor``.
        """
        stmt = self._create_sdm_link_collection_query(
            include_schemas=False, include_tables=True, include_columns=False
        ).where(
            literal_column("combined_links.schema_name") == schema_name,
        )

        runner = CountedPaginatedQueryRunner(
            entry_type=SdmTableLinksCollection,
            cursor_type=SdmTableLinksCollectionCursor,
        )
        return await runner.query_row(
            session=self._session,
            stmt=stmt,
            cursor=cursor,
            limit=limit,
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
        """Get a collection of SDM entities and their links.

        This method can potentially return links to schemas, tables, and
        columns. It can also be scoped to a specific schema. The results are
        sorted alphabetically across schema name, table name, and column name.
        """
        stmt = self._create_sdm_link_collection_query(
            include_schemas=include_schemas,
            include_tables=include_tables,
            include_columns=include_columns,
        )
        if schema_name:
            stmt = stmt.where(
                literal_column("combined_links.schema_name") == schema_name
            )

        runner = CountedPaginatedQueryRunner(
            entry_type=SdmLinksCollection,
            cursor_type=SdmLinksCollectionCursor,
        )
        return await runner.query_row(
            session=self._session,
            stmt=stmt,
            cursor=cursor,
            limit=limit,
        )

    def _create_sdm_link_collection_query(
        self,
        *,
        include_schemas: bool,
        include_tables: bool,
        include_columns: bool,
    ) -> Select:
        """Create a select that can query a links for a collection of some
        combination of schema, table, and column entity types.
        """
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
        ).select_from(
            union_all(
                select(schema_links_cte),
                select(table_links_cte),
                select(column_links_cte),
            ).alias("combined_links")
        )

        if include_schemas and include_tables and include_columns:
            # All three entity types
            stmt = stmt.where(
                or_(
                    literal_column("combined_links.entity_type") == "schema",
                    literal_column("combined_links.entity_type") == "table",
                    literal_column("combined_links.entity_type") == "column",
                )
            )
        elif include_schemas and include_tables and not include_columns:
            # Schemas and tables only
            stmt = stmt.where(
                or_(
                    literal_column("combined_links.entity_type") == "schema",
                    literal_column("combined_links.entity_type") == "table",
                )
            )
        elif include_schemas and not include_tables and include_columns:
            # Schemas and columns only
            stmt = stmt.where(
                or_(
                    literal_column("combined_links.entity_type") == "schema",
                    literal_column("combined_links.entity_type") == "column",
                )
            )
        elif not include_schemas and include_tables and include_columns:
            # Tables and columns only
            stmt = stmt.where(
                or_(
                    literal_column("combined_links.entity_type") == "table",
                    literal_column("combined_links.entity_type") == "column",
                )
            )
        elif include_schemas and not include_tables and not include_columns:
            # Schemas only
            stmt = stmt.where(
                literal_column("combined_links.entity_type") == "schema"
            )
        elif not include_schemas and include_tables and not include_columns:
            # Tables only
            stmt = stmt.where(
                literal_column("combined_links.entity_type") == "table"
            )
        elif not include_schemas and not include_tables and include_columns:
            # Columns only
            stmt = stmt.where(
                literal_column("combined_links.entity_type") == "column"
            )
        else:
            # No entities selected - raise an error
            raise ValueError("schemas, tables, or columns must be selected")

        # Grouping for the link aggregation
        return stmt.group_by(
            text("domain"),
            text("schema_name"),
            text("entity_type"),
            text("table_name"),
            text("column_name"),
            text("tap_table_index"),
            text("tap_column_index"),
        )

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
                # Empty string to allow alphabetic sorting
                literal("").label("column_name"),
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
                # Empty string to allow alphabetic sorting
                literal("").label("table_name"),
                literal("").label("column_name"),
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
    """The tap_column_index of the first entry."""

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
        column_name_col: ColumnElement[str] = literal_column("column_name")
        tap_column_index_col: ColumnElement[str] = literal_column(
            "tap_column_index"
        )

        if reverse:
            # For reverse order, NULL comes first since it's infinitely large
            stmt = stmt.order_by(
                tap_column_index_col.desc().nulls_first(),
                column_name_col.desc(),
            )
        else:
            # For forward order, NULL comes last since it's infinitely large
            stmt = stmt.order_by(
                tap_column_index_col.asc().nulls_last(),
                column_name_col,
            )
        return stmt

    @override
    def apply_cursor(self, stmt: Select) -> Select:
        """Apply the restrictions from the cursor to a select statement."""
        column_name_col: ColumnElement[str] = literal_column("column_name")
        tap_column_index_col: ColumnElement[str] = literal_column(
            "tap_column_index"
        )

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
                        tap_column_index_col.is_not(None),
                        and_(
                            tap_column_index_col.is_(None),
                            column_name_col < self.column_name,
                        ),
                    )
                )
            else:
                # When moving forwards from a NULL position, only get rows with
                # NULL tap_column_index and larger column name
                stmt = stmt.where(
                    and_(
                        tap_column_index_col.is_(None),
                        column_name_col >= self.column_name,
                    )
                )
        # Handle cases where tap_column_index is not NULL
        elif self.previous:
            # Going backwards
            stmt = stmt.where(
                or_(
                    # Smaller index values
                    tap_column_index_col < self.tap_column_index,
                    # Same index value, smaller column name
                    and_(
                        tap_column_index_col == self.tap_column_index,
                        column_name_col < self.column_name,
                    ),
                )
            )
        else:
            # Going forwards
            stmt = stmt.where(
                or_(
                    # NULL values (they're largest)
                    tap_column_index_col.is_(None),
                    # Larger index values
                    tap_column_index_col >= self.tap_column_index,
                    # Same index value, larger column name
                    and_(
                        tap_column_index_col == self.tap_column_index,
                        column_name_col >= self.column_name,
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


@dataclass(slots=True)
class SdmTableLinksCollectionCursor(PaginationCursor[SdmTableLinksCollection]):
    """A pagination cursor for SdmTableLinksCollection results.

    The cursor involves the tap_table_index and table_name of the SDM
    table. Our compound sorting order of tables is:

    1. tap_table_index in increasing order, treating NULL as infinitely
       large.
    2. table_name in increasing alphabetic order for a given
       tap_table_index.
    """

    tap_table_index: int | None

    table_name: str

    @override
    @classmethod
    def from_entry(
        cls, entry: SdmTableLinksCollection, *, reverse: bool = False
    ) -> Self:
        """Construct a cursor with an entry as the bound."""
        # The cursor is a tuple of the schema name, table name, and column name
        # in natural order.
        return cls(
            tap_table_index=entry.tap_table_index,
            table_name=entry.table_name,
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
                tap_table_index=data["tap_table_index"],
                table_name=data["table_name"],
                previous=data["previous"],
            )
        except Exception as e:
            raise InvalidCursorError(f"Cannot parse cursor: {e!s}") from e

    @override
    @classmethod
    def apply_order(cls, stmt: Select, *, reverse: bool = False) -> Select:
        """Apply the sort order of the cursor to a select statement."""
        table_name_col: ColumnElement[str] = literal_column("table_name")
        tap_table_index_col: ColumnElement[str] = literal_column(
            "tap_table_index"
        )

        if reverse:
            # For reverse order, NULL comes first since it's infinitely
            # large
            stmt = stmt.order_by(
                tap_table_index_col.desc().nulls_first(),
                table_name_col.desc(),
            )
        else:
            # For forward order, NULL comes last since it's infinitely
            # large
            stmt = stmt.order_by(
                tap_table_index_col.asc().nulls_last(),
                table_name_col,
            )
        return stmt

    @override
    def apply_cursor(self, stmt: Select) -> Select:
        """Apply the restrictions from the cursor to a select statement."""
        table_name_col: ColumnElement[str] = literal_column("table_name")
        tap_table_index_col: ColumnElement[str] = literal_column(
            "tap_table_index"
        )

        # Specially handle NULL in tap_table_index because we treat it as
        # infinitely large for ordering purposes.
        if self.tap_table_index is None:
            # Cursor is at a NULL position
            if self.previous:
                # When moving backwards from a NULL position, get all non-NULL
                # rows or rows with NULL tap_table_index but smaller table name
                stmt = stmt.where(
                    or_(
                        tap_table_index_col.is_not(None),
                        and_(
                            tap_table_index_col.is_(None),
                            table_name_col < self.table_name,
                        ),
                    )
                )
            else:
                # When moving forwards from a NULL position, only get rows with
                # NULL tap_table_index and larger table name
                stmt = stmt.where(
                    and_(
                        tap_table_index_col.is_(None),
                        table_name_col >= self.table_name,
                    )
                )
        # Handle cases where tap_table_index is not NULL
        elif self.previous:
            # Going backwards
            stmt = stmt.where(
                or_(
                    # Smaller index values
                    tap_table_index_col < self.tap_table_index,
                    # Same index value, smaller table name
                    and_(
                        tap_table_index_col == self.tap_table_index,
                        table_name_col < self.table_name,
                    ),
                )
            )
        else:
            # Going forwards
            stmt = stmt.where(
                or_(
                    # NULL values (they're largest)
                    tap_table_index_col.is_(None),
                    # Larger index values
                    tap_table_index_col > self.tap_table_index,
                    # Same index value, larger table name
                    and_(
                        tap_table_index_col == self.tap_table_index,
                        table_name_col >= self.table_name,
                    ),
                )
            )
        return stmt

    @override
    def invert(self) -> Self:
        return type(self)(
            tap_table_index=self.tap_table_index,
            table_name=self.table_name,
            previous=not self.previous,
        )

    def __str__(self) -> str:
        """Serialize to string.

        This is the reverse of from_str.
        """
        data = {
            "tap_table_index": self.tap_table_index,
            "table_name": self.table_name,
            "previous": self.previous,
        }
        # encode base64
        encoded = base64.b64encode(json.dumps(data).encode("utf-8"))
        return encoded.decode("utf-8")


@dataclass(slots=True)
class SdmLinksCollectionCursor(PaginationCursor[SdmLinksCollection]):
    """A pagination cursor for SdmLinksCollection results.

    This cursor sorts SDM entities by name, with a compund key of:

    1. schema_name in increasing order
    2. table_name in increasing order
    3. column_name in increasing order

    For schemas and tables, empty strings are used for table_name and
    column_name (respectively) to allow alphabetic sorting.
    """

    schema_name: str

    table_name: str

    column_name: str

    @override
    @classmethod
    def from_entry(
        cls, entry: SdmLinksCollection, *, reverse: bool = False
    ) -> Self:
        """Construct a cursor with an entry as the bound."""
        match entry.root:
            case SdmSchemaLinksCollection():
                # Schema links
                return cls(
                    schema_name=entry.root.schema_name,
                    table_name="",
                    column_name="",
                    previous=reverse,
                )
            case SdmTableLinksCollection():
                # Table links
                return cls(
                    schema_name=entry.root.schema_name,
                    table_name=entry.root.table_name,
                    column_name="",
                    previous=reverse,
                )
            case SdmColumnLinksCollection():
                # Column links
                return cls(
                    schema_name=entry.root.schema_name,
                    table_name=entry.root.table_name,
                    column_name=entry.root.column_name,
                    previous=reverse,
                )

    @override
    @classmethod
    def from_str(cls, cursor: str) -> Self:
        """Build cursor from the string serialization form."""
        try:
            decoded = base64.b64decode(cursor).decode("utf-8")
            data = json.loads(decoded)
            return cls(
                schema_name=data["schema_name"],
                table_name=data["table_name"],
                column_name=data["column_name"],
                previous=data["previous"],
            )
        except Exception as e:
            raise InvalidCursorError(f"Cannot parse cursor: {e!s}") from e

    @override
    @classmethod
    def apply_order(cls, stmt: Select, *, reverse: bool = False) -> Select:
        """Apply the sort order of the cursor to a select statement."""
        # Create references to the columns from the CTE
        schema_name_col: ColumnElement[str] = literal_column("schema_name")
        table_name_col: ColumnElement[str] = literal_column("table_name")
        column_name_col: ColumnElement[str] = literal_column("column_name")
        if reverse:
            # For reverse order
            stmt = stmt.order_by(
                schema_name_col.desc(),
                table_name_col.desc(),
                column_name_col.desc(),
            )
        else:
            # For forward order
            stmt = stmt.order_by(
                schema_name_col,
                table_name_col,
                column_name_col,
            )
        return stmt

    @override
    def apply_cursor(self, stmt: Select) -> Select:
        """Apply the restrictions from the cursor to a select statement."""
        # Create references to the columns from the CTE
        schema_name_col: ColumnElement[str] = literal_column("schema_name")
        table_name_col: ColumnElement[str | None] = literal_column(
            "table_name"
        )
        column_name_col: ColumnElement[str | None] = literal_column(
            "column_name"
        )

        if self.previous:
            stmt = stmt.where(
                or_(
                    schema_name_col < self.schema_name,
                    and_(
                        schema_name_col == self.schema_name,
                        or_(
                            table_name_col < self.table_name,
                            and_(
                                table_name_col == self.table_name,
                                column_name_col < self.column_name,
                            ),
                        ),
                    ),
                )
            )
        else:
            stmt = stmt.where(
                or_(
                    schema_name_col > self.schema_name,
                    and_(
                        schema_name_col == self.schema_name,
                        or_(
                            table_name_col > self.table_name,
                            and_(
                                table_name_col == self.table_name,
                                column_name_col >= self.column_name,
                            ),
                        ),
                    ),
                )
            )

        return stmt

    @override
    def invert(self) -> Self:
        return type(self)(
            schema_name=self.schema_name,
            table_name=self.table_name,
            column_name=self.column_name,
            previous=not self.previous,
        )

    def __str__(self) -> str:
        """Serialize to string.

        This is the reverse of from_str.
        """
        data = {
            "schema_name": self.schema_name,
            "table_name": self.table_name,
            "column_name": self.column_name,
            "previous": self.previous,
        }
        # encode base64
        encoded = base64.b64encode(json.dumps(data).encode("utf-8"))
        return encoded.decode("utf-8")
