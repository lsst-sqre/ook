"""Storage interface for resource information."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self, override

from pydantic import AnyHttpUrl
from safir.database import (
    CountedPaginatedList,
    CountedPaginatedQueryRunner,
    PaginationCursor,
)
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import async_scoped_session
from sqlalchemy.orm import selectinload
from structlog.stdlib import BoundLogger

from ook.dbschema.resources import SqlDocumentResource, SqlResource
from ook.domain.resources import (
    Contributor,
    Document,
    ExternalRelation,
    RelationType,
    Resource,
    ResourceClass,
    ResourceRelation,
    ResourceType,
)

from ._models import ResourceLoadOptions, ResourcePaginationModel

__all__ = ["ResourceStore", "ResourcesCursor"]


class ResourceStore:
    """Interface for storing resource information in a database."""

    def __init__(
        self,
        session: async_scoped_session,
        logger: BoundLogger,
    ) -> None:
        self._session = session
        self._logger = logger

    async def get_resource_by_id(
        self,
        resource_id: int,
        load_options: ResourceLoadOptions | None = None,
    ) -> Resource | None:
        """Get a resource by its ID.

        Parameters
        ----------
        resource_id
            The ID of the resource to retrieve.
        load_options
            Options for loading related data. Defaults to loading no related
            data.

        Returns
        -------
        Resource or None
            The resource with the specified ID, or None if not found.
        """
        if load_options is None:
            load_options = ResourceLoadOptions.none()

        # Use SQLAlchemy's polymorphic loading
        stmt = select(SqlResource).where(SqlResource.id == resource_id)

        # Add eager loading for relationships as needed
        if load_options.include_contributors:
            stmt = stmt.options(selectinload(SqlResource.contributors))
        if (
            load_options.include_resource_relations
            or load_options.include_external_relations
        ):
            stmt = stmt.options(selectinload(SqlResource.relations))

        result = await self._session.execute(stmt)
        sql_resource = result.scalar_one_or_none()

        if sql_resource is None:
            return None

        return self._sql_to_domain(sql_resource)

    async def get_resources(
        self,
        cursor: ResourcesCursor | None = None,
        limit: int | None = None,
    ) -> CountedPaginatedList[Resource, ResourcesCursor]:
        """Get a list of resources with pagination.

        Parameters
        ----------
        cursor
            The pagination cursor for the query.
        limit
            The maximum number of resources to return.

        Returns
        -------
        CountedPaginatedList[Resource]
            A paginated list of resources.
        """
        # For bulk queries, select only basic fields for pagination efficiency
        # as the `ResourcePaginationModel` for use with Safir's
        # CountedPaginatedQueryRunner. We use that paginated list to get the
        # resource IDs, and then fetch the full resources in a second query.
        stmt = select(
            SqlResource.id,
            SqlResource.resource_class,
            SqlResource.date_created,
            SqlResource.date_updated,
            SqlResource.title,
        )

        runner = CountedPaginatedQueryRunner(
            entry_type=ResourcePaginationModel,
            cursor_type=ResourcesCursor,
        )

        paginated_basic = await runner.query_row(
            session=self._session,
            stmt=stmt,
            cursor=cursor,
            limit=limit,
        )

        # Now fetch the full resources using polymorphic loading
        resource_ids = [entry.id for entry in paginated_basic.entries]

        if not resource_ids:
            return CountedPaginatedList(
                entries=[],
                count=paginated_basic.count,
                next_cursor=paginated_basic.next_cursor,
                prev_cursor=paginated_basic.prev_cursor,
            )

        full_stmt = select(SqlResource).where(SqlResource.id.in_(resource_ids))
        result = await self._session.execute(full_stmt)
        sql_resources = result.scalars().all()

        # Maintain order from pagination
        sql_resource_map = {r.id: r for r in sql_resources}
        ordered_sql_resources = [
            sql_resource_map[resource_id] for resource_id in resource_ids
        ]

        # Convert to domain models
        domain_resources = [
            self._sql_to_domain(sql_resource)
            for sql_resource in ordered_sql_resources
        ]

        return CountedPaginatedList(
            entries=domain_resources,
            count=paginated_basic.count,
            next_cursor=paginated_basic.next_cursor,
            prev_cursor=paginated_basic.prev_cursor,
        )

    def _sql_to_domain(self, sql_resource: SqlResource) -> Resource:
        """Convert a SQLAlchemy resource model to a domain model.

        Parameters
        ----------
        sql_resource
            The SQLAlchemy resource model to convert.

        Returns
        -------
        Resource
            The converted domain resource.
        """
        # Convert basic fields common to all resources
        resource_type = (
            ResourceType(sql_resource.type) if sql_resource.type else None
        )

        # Convert contributors if loaded
        if hasattr(sql_resource, "contributors") and sql_resource.contributors:
            contributors = [
                Contributor.model_validate(
                    sql_contributor, from_attributes=True
                )
                for sql_contributor in sql_resource.contributors
            ]
        else:
            contributors = None

        # Convert resource relations if loaded
        resource_relations: list[ResourceRelation] | None = None
        if hasattr(sql_resource, "relations") and sql_resource.relations:
            resource_relations = []
            for relation in sql_resource.relations:
                if relation.related_resource_id is not None:
                    resource_relations.append(
                        ResourceRelation(
                            relation_type=RelationType(relation.relation_type),
                            resource_id=relation.related_resource_id,
                        )
                    )

        # Convert external relations if loaded
        external_relations: list[ExternalRelation] | None = None
        if hasattr(sql_resource, "relations") and sql_resource.relations:
            external_relations = [
                ExternalRelation(
                    relation_type=RelationType(relation.relation_type),
                    external_reference=ExternalReference.model_validate(
                        relation.related_external_reference,
                        from_attributes=True,
                    ),
                )
                for relation in sql_resource.relations
                if relation.related_external_ref_id is not None
            ]

        # Polymorphic conversion based on the actual SQL model type
        if isinstance(sql_resource, SqlDocumentResource):
            return Document(
                id=sql_resource.id,
                date_created=sql_resource.date_created,
                date_updated=sql_resource.date_updated,
                title=sql_resource.title,
                description=sql_resource.description,
                url=AnyHttpUrl(sql_resource.url) if sql_resource.url else None,
                doi=sql_resource.doi,
                date_resource_published=sql_resource.date_resource_published,
                date_resource_updated=sql_resource.date_resource_updated,
                version=sql_resource.version,
                type=resource_type,
                series=sql_resource.series,
                handle=sql_resource.handle,
                generator=sql_resource.generator,
                contributors=contributors,
                resource_relations=resource_relations,
                external_relations=external_relations,
            )
        else:
            # Generic resource
            resource_class = ResourceClass(
                sql_resource.resource_class or "generic"
            )
            return Resource(
                id=sql_resource.id,
                resource_class=resource_class,
                date_created=sql_resource.date_created,
                date_updated=sql_resource.date_updated,
                title=sql_resource.title,
                description=sql_resource.description,
                url=AnyHttpUrl(sql_resource.url) if sql_resource.url else None,
                doi=sql_resource.doi,
                date_resource_published=sql_resource.date_resource_published,
                date_resource_updated=sql_resource.date_resource_updated,
                version=sql_resource.version,
                type=resource_type,
                contributors=contributors,
                resource_relations=resource_relations,
                external_relations=external_relations,
            )


@dataclass(slots=True)
class ResourcesCursor(PaginationCursor[ResourcePaginationModel]):
    """Cursor for paginating resources, sorted by their ID."""

    resource_id: int
    """The ID of the resource."""

    @override
    @classmethod
    def from_entry(
        cls, entry: ResourcePaginationModel, *, reverse: bool = False
    ) -> Self:
        """Create a cursor from a resource entry as the bound."""
        return cls(resource_id=entry.id, previous=reverse)

    @override
    @classmethod
    def from_str(cls, cursor: str) -> Self:
        """Create a cursor from a string."""
        previous_prefix = "p__"
        if cursor.startswith(previous_prefix):
            resource_id_str = cursor.removeprefix(previous_prefix)
            previous = True
        else:
            resource_id_str = cursor
            previous = False
        return cls(resource_id=int(resource_id_str), previous=previous)

    @override
    @classmethod
    def apply_order(cls, stmt: Select, *, reverse: bool = False) -> Select:
        """Apply the sort order of the cursor to a select statement."""
        return stmt.order_by(
            SqlResource.id.desc() if reverse else SqlResource.id.asc()
        )

    @override
    def apply_cursor(self, stmt: Select) -> Select:
        """Apply the cursor to a select statement."""
        if self.previous:
            return stmt.where(SqlResource.id < self.resource_id)
        return stmt.where(SqlResource.id >= self.resource_id)

    @override
    def invert(self) -> Self:
        """Invert the cursor."""
        return type(self)(
            resource_id=self.resource_id, previous=not self.previous
        )

    def __str__(self) -> str:
        """Convert the cursor to a string."""
        return (
            f"p__{self.resource_id}"
            if self.previous
            else str(self.resource_id)
        )
