"""Resource management service."""

from __future__ import annotations

from safir.database import CountedPaginatedList
from structlog.stdlib import BoundLogger

from ook.domain.resources import Document, Resource
from ook.storage.resourcestore import (
    ResourceLoadOptions,
    ResourcesCursor,
    ResourceStore,
)

__all__ = ["ResourceService"]


class ResourceService:
    """Service for managing bibliographic resources.

    Parameters
    ----------
    resource_store
        The resource store, which interfaces with the database.
    logger
        The logger.
    """

    def __init__(
        self, *, resource_store: ResourceStore, logger: BoundLogger
    ) -> None:
        self._resource_store = resource_store
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
        return await self._resource_store.get_resource_by_id(
            resource_id, load_options
        )

    async def get_resources(
        self,
        *,
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
        return await self._resource_store.get_resources(cursor, limit)

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
        await self._resource_store.upsert_document(
            document, delete_stale_relations=delete_stale_relations
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
        await self._resource_store.upsert_resource(
            resource, delete_stale_relations=delete_stale_relations
        )
