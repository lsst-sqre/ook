"""Storage interface for resource information."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self, override

from safir.database import (
    CountedPaginatedList,
    CountedPaginatedQueryRunner,
    PaginationCursor,
)
from safir.datetime import current_datetime
from sqlalchemy import Select, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_scoped_session
from structlog.stdlib import BoundLogger

from ook.dbschema import Base
from ook.dbschema.authors import SqlAuthor
from ook.dbschema.resources import (
    SqlContributor,
    SqlDocumentResource,
    SqlExternalReference,
    SqlResource,
    SqlResourceRelation,
)
from ook.domain.resources import (
    Contributor,
    Document,
    ExternalReference,
    ExternalRelation,
    RelatedExternalReference,
    RelatedResourceSummary,
    Resource,
    ResourceRelation,
    ResourceSummary,
)

from ._models import ResourcePaginationModel
from ._queryutils import create_resource_with_relations_stmt

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
    ) -> Resource | None:
        """Get a resource by its ID with all related data loaded.

        Parameters
        ----------
        resource_id
            The ID of the resource to retrieve.

        Returns
        -------
        Resource or None
            The resource with the specified ID, or None if not found.
        """
        stmt = create_resource_with_relations_stmt(resource_id)
        result = (await self._session.execute(stmt)).first()

        if result is None:
            return None

        self._logger.debug(
            "Fetched resource by ID",
            resource_id=resource_id,
            result=result,
        )

        # Use Pydantic's from_attributes to directly validate the result
        sql_resource = result[0]
        contributors = result[1] if result[1] else []
        resource_relations_json = result[2] if result[2] else []
        external_relations = result[3] if result[3] else []

        self._logger.debug(
            "Resource query result",
            sql_resource=sql_resource,
            contributors_count=len(contributors),
            contributors=contributors,
            resource_relations_count=len(resource_relations_json),
            external_relations_count=len(external_relations),
        )

        # Process related resources for the API response format
        related_resources: list[
            RelatedResourceSummary | RelatedExternalReference
        ] = []

        # Process internal resource relations
        for rel_data in resource_relations_json:
            resource_summary = ResourceSummary.model_validate(
                rel_data["resource"]
            )
            related_resource = RelatedResourceSummary(
                relation_type=rel_data["relation_type"],
                resource=resource_summary,
            )
            related_resources.append(related_resource)

        # Process external relations
        for ext_rel in external_relations:
            related_external = RelatedExternalReference.model_validate(ext_rel)
            related_resources.append(related_external)

        # Create resource_relations and external_relations for
        # backward compatibility
        resource_relations = [
            ResourceRelation(
                relation_type=rel_data["relation_type"],
                resource_id=rel_data["resource"]["id"],
            )
            for rel_data in resource_relations_json
        ]

        # Create a combined data structure for Pydantic validation
        # First get all attributes from the base resource table
        combined_data = {
            key: value
            for key, value in sql_resource.__dict__.items()
            if not key.startswith("_")  # Skip private attributes
        }
        # Second, add the aggregated fields
        combined_data.update(
            {
                "contributors": contributors,
                "resource_relations": resource_relations,
                "external_relations": external_relations,
                "related_resources": related_resources,
            }
        )

        if isinstance(sql_resource, SqlDocumentResource):
            return Document.model_validate(combined_data, from_attributes=True)
        else:
            return Resource.model_validate(combined_data, from_attributes=True)

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

        # Fetch full resources with all related data for each ID
        resources = []
        for resource_id in resource_ids:
            resource = await self.get_resource_by_id(resource_id)
            if resource:
                resources.append(resource)

        return CountedPaginatedList(
            entries=resources,
            count=paginated_basic.count,
            next_cursor=paginated_basic.next_cursor,
            prev_cursor=paginated_basic.prev_cursor,
        )

    async def upsert_document(
        self,
        document: Document,
        *,
        delete_stale_relations: bool = True,
    ) -> None:
        """Upsert a document resource into the database.

        Parameters
        ----------
        document
            The document to upsert.
        delete_stale_relations
            If True, delete existing contributors and relations before
            inserting new ones.
        """
        now = current_datetime(microseconds=False)

        # Handle related data deletion first
        if delete_stale_relations:
            await self._delete_resource_relations(document.id)

        # Use joined inheritance upsert for document resource
        await self._upsert_joined_inheritance(
            child_model=SqlDocumentResource,
            parent_model=SqlResource,
            join_conditions={
                "id": document.id,
            },
            update_fields={
                "date_updated": now,
                "title": document.title,
                "description": document.description,
                "url": str(document.url) if document.url else None,
                "doi": document.doi,
                "date_resource_published": document.date_resource_published,
                "date_resource_updated": document.date_resource_updated,
                "version": document.version,
                "type": document.type.value if document.type else None,
                "series": document.series,
                "handle": document.handle,
                "generator": document.generator,
            },
            insert_fields={
                "id": document.id,
                "resource_class": document.resource_class.value,
                "date_created": document.date_created,
                "date_updated": now,
                "title": document.title,
                "description": document.description,
                "url": str(document.url) if document.url else None,
                "doi": document.doi,
                "date_resource_published": document.date_resource_published,
                "date_resource_updated": document.date_resource_updated,
                "version": document.version,
                "type": document.type.value if document.type else None,
                "series": document.series,
                "handle": document.handle,
                "generator": document.generator,
            },
        )

        await self._upsert_contributors(document.id, document.contributors)
        await self._upsert_relations(
            document.id,
            document.resource_relations,
            document.external_relations,
        )

    async def upsert_resource(
        self,
        resource: Resource,
        *,
        delete_stale_relations: bool = True,
    ) -> None:
        """Upsert a generic resource into the database.

        Parameters
        ----------
        resource
            The resource to upsert.
        delete_stale_relations
            If True, delete existing contributors and relations before
            inserting new ones.
        """
        now = current_datetime(microseconds=False)

        # Build resource data with common fields only
        resource_data = self._build_common_resource_data(resource, now)

        # Upsert the resource using PostgreSQL's ON CONFLICT
        insert_stmt = pg_insert(SqlResource).values([resource_data])
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=["id"],
            set_=self._get_common_resource_update_fields(insert_stmt),
        )
        await self._session.execute(upsert_stmt)
        await self._session.flush()

        # Handle related data
        if delete_stale_relations:
            await self._delete_resource_relations(resource.id)

        await self._upsert_contributors(resource.id, resource.contributors)
        await self._upsert_relations(
            resource.id,
            resource.resource_relations,
            resource.external_relations,
        )

    def _build_common_resource_data(
        self, resource: Resource, timestamp: datetime
    ) -> dict[str, Any]:
        """Build common resource data dictionary for upserts.

        This method should be synchronized with the fields in
        `_get_common_resource_update_fields` and with the SqlResource model.

        Parameters
        ----------
        resource
            The resource domain model.
        timestamp
            The timestamp to use for date_updated.

        Returns
        -------
        dict[str, Any]
            Dictionary of common resource fields.
        """
        return {
            "id": resource.id,
            "resource_class": resource.resource_class.value,
            "date_updated": timestamp,
            "title": resource.title,
            "description": resource.description,
            "url": str(resource.url) if resource.url else None,
            "doi": resource.doi,
            "date_resource_published": resource.date_resource_published,
            "date_resource_updated": resource.date_resource_updated,
            "version": resource.version,
            "type": resource.type.value if resource.type else None,
        }

    def _get_common_resource_update_fields(
        self, insert_stmt: Any
    ) -> dict[str, Any]:
        """Get common resource fields for ON CONFLICT DO UPDATE.

        This method should be synchronized with the fields in
        `_build_common_resource_data` and with the SqlResource model.

        Parameters
        ----------
        insert_stmt
            The PostgreSQL insert statement.

        Returns
        -------
        dict[str, Any]
            Dictionary of field updates for common resource fields.
        """
        return {
            "resource_class": insert_stmt.excluded.resource_class,
            "date_updated": insert_stmt.excluded.date_updated,
            "title": insert_stmt.excluded.title,
            "description": insert_stmt.excluded.description,
            "url": insert_stmt.excluded.url,
            "doi": insert_stmt.excluded.doi,
            "date_resource_published": (
                insert_stmt.excluded.date_resource_published
            ),
            "date_resource_updated": (
                insert_stmt.excluded.date_resource_updated
            ),
            "version": insert_stmt.excluded.version,
            "type": insert_stmt.excluded.type,
        }

    async def _delete_resource_relations(self, resource_id: int) -> None:
        """Delete all existing relations for a resource.

        Parameters
        ----------
        resource_id
            The ID of the resource whose relations to delete.
        """
        # Delete contributors
        await self._session.execute(
            delete(SqlContributor).where(
                SqlContributor.resource_id == resource_id
            )
        )

        # Delete resource relations
        await self._session.execute(
            delete(SqlResourceRelation).where(
                SqlResourceRelation.source_resource_id == resource_id
            )
        )

    async def _upsert_contributors(
        self, resource_id: int, contributors: list[Contributor] | None
    ) -> None:
        """Upsert contributors for a resource.

        Parameters
        ----------
        resource_id
            The ID of the resource.
        contributors
            List of contributors to associate with the resource.
        """
        if not contributors:
            return

        # In order to upsert contributors, we need to resolve their database
        # primary keys given the `internal_id` field on the Author domain
        # model. This is done as a SQL query prior to the contributor upsert.

        author_internal_ids = list(
            {contributor.author.internal_id for contributor in contributors}
        )

        # Query to get the mapping from internal_id to database id
        author_query = select(SqlAuthor.id, SqlAuthor.internal_id).where(
            SqlAuthor.internal_id.in_(author_internal_ids)
        )

        author_result = await self._session.execute(author_query)
        author_id_map = {
            internal_id: db_id for db_id, internal_id in author_result
        }

        # Build contributor data using the resolved author IDs
        contributor_data = []
        for contributor in contributors:
            author_db_id = author_id_map.get(contributor.author.internal_id)
            if author_db_id is None:
                # Log warning or raise error if author not found
                self._logger.warning(
                    "Author not found in database",
                    internal_id=contributor.author.internal_id,
                    resource_id=resource_id,
                )
                continue

            contributor_data.append(
                {
                    "resource_id": resource_id,
                    "order": contributor.order,
                    "role": contributor.role.value,
                    "author_id": author_db_id,
                }
            )

        if contributor_data:
            self._logger.debug(
                "Upserting contributors",
                resource_id=resource_id,
                contributor_count=len(contributor_data),
                contributor_data=contributor_data,
            )
            await self._session.execute(
                pg_insert(SqlContributor).values(contributor_data)
            )

    async def _upsert_relations(
        self,
        resource_id: int,
        resource_relations: list[ResourceRelation] | None,
        external_relations: list[ExternalRelation] | None,
    ) -> None:
        """Upsert relations for a resource.

        Parameters
        ----------
        resource_id
            The ID of the resource.
        resource_relations
            List of resource relations.
        external_relations
            List of external relations.
        """
        relation_data = []

        # Add resource relations
        if resource_relations:
            relation_data.extend(
                [
                    {
                        "source_resource_id": resource_id,
                        "related_resource_id": relation.resource_id,
                        "related_external_ref_id": None,
                        "relation_type": relation.relation_type.value,
                    }
                    for relation in resource_relations
                ]
            )

        # Add external relations
        if external_relations:
            for relation in external_relations:
                # Upsert the external reference first
                ext_ref_id = await self._upsert_external_reference(
                    relation.external_reference
                )

                relation_data.append(
                    {
                        "source_resource_id": resource_id,
                        "related_resource_id": None,
                        "related_external_ref_id": ext_ref_id,
                        "relation_type": relation.relation_type.value,
                    }
                )

        if relation_data:
            await self._session.execute(
                pg_insert(SqlResourceRelation).values(relation_data)
            )

    async def _upsert_external_reference(
        self, external_ref: ExternalReference
    ) -> int:
        """Upsert an external reference and return its ID.

        Parameters
        ----------
        external_ref
            The external reference to upsert.

        Returns
        -------
        int
            The ID of the upserted external reference.
        """
        ext_ref_data = {
            "url": external_ref.url,
            "doi": external_ref.doi,
            "arxiv_id": external_ref.arxiv_id,
            "isbn": external_ref.isbn,
            "issn": external_ref.issn,
            "ads_bibcode": external_ref.ads_bibcode,
            "type": external_ref.type.value if external_ref.type else None,
            "title": external_ref.title,
            "publication_year": external_ref.publication_year,
            "volume": external_ref.volume,
            "issue": external_ref.issue,
            "number": external_ref.number,
            "number_type": external_ref.number_type
            if external_ref.number_type
            else None,
            "first_page": external_ref.first_page,
            "last_page": external_ref.last_page,
            "publisher": external_ref.publisher,
            "edition": external_ref.edition,
            "contributors": [c.model_dump() for c in external_ref.contributors]
            if external_ref.contributors
            else None,
        }

        insert_stmt = pg_insert(SqlExternalReference).values([ext_ref_data])

        # Use DOI for conflict resolution if available, otherwise URL
        if external_ref.doi:
            upsert_stmt = insert_stmt.on_conflict_do_update(
                index_elements=["doi"], set_=ext_ref_data
            ).returning(SqlExternalReference.id)
        elif external_ref.url:
            upsert_stmt = insert_stmt.on_conflict_do_update(
                index_elements=["url"], set_=ext_ref_data
            ).returning(SqlExternalReference.id)
        else:
            # No unique identifier, just insert
            upsert_stmt = insert_stmt.returning(SqlExternalReference.id)

        result = await self._session.execute(upsert_stmt)
        return result.scalar_one()

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
