"""The documentation links service."""

from __future__ import annotations

from structlog.stdlib import BoundLogger

from ook.domain.links import SdmSchemaLink
from ook.storage.sdmschemaslinkstore import SdmSchemasLinkStore

__all__ = ["LinksService"]


class LinksService:
    """A service for linking to documentation across known domains."""

    def __init__(
        self, logger: BoundLogger, sdm_schemas_link_store: SdmSchemasLinkStore
    ) -> None:
        self._logger = logger
        self._sdm_schemas_link_store = sdm_schemas_link_store

    async def get_links_for_sdm_schema(
        self, schema_name: str
    ) -> SdmSchemaLink | None:
        """Get links for an SDM schema."""
        return await self._sdm_schemas_link_store.get_schema(schema_name)
