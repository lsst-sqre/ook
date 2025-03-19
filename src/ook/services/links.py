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
    ) -> list[SdmSchemaLink] | None:
        """Get links for an SDM schema."""
        sdm_links = await self._sdm_schemas_link_store.get_schema(schema_name)
        if sdm_links is None:
            return None
        # In the future we may have more than one link to a schema. For now,
        # we'll just return the one link to sdm-schemas.lsst.io because
        # the SdmSchemasLinkStore only returns one link.
        return [sdm_links]
