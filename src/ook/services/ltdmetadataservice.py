"""A service that gets documentation project metadata from the LTD v1 API."""

from __future__ import annotations

from httpx import AsyncClient
from structlog.stdlib import BoundLogger


class LtdMetadataService:
    """A service that gets documentation project metadata from the LTD v1
    API.
    """

    def __init__(
        self, *, logger: BoundLogger, http_client: AsyncClient
    ) -> None:
        self._logger = logger
        self._http_client = http_client

    def get_product_api_url(self, product_slug: str) -> str:
        """Get the LTD API URL for a given product slug."""
        return f"https://keeper.lsst.codes/products/{product_slug}"

    async def get_project(self, product_slug: str) -> dict:
        """Get the LTD project metadata for a given product slug."""
        url = self.get_product_api_url(product_slug)
        response = await self._http_client.get(url)
        response.raise_for_status()
        return response.json()

    async def get_edition(
        self, product_slug: str, edition_slug: str = "main"
    ) -> dict:
        """Get the LTD edition metadata for a given product and edition."""
        editions_url = (
            f"https://keeper.lsst.codes/products/{product_slug}/editions/"
        )
        response = await self._http_client.get(editions_url)
        response.raise_for_status()
        editions = response.json()
        for edition_url in editions["editions"]:
            response = await self._http_client.get(edition_url)
            response.raise_for_status()
            edition = response.json()
            if edition["slug"] == edition_slug:
                return edition
        raise RuntimeError(
            f"Could not find edition {edition_slug} for product {product_slug}"
        )
