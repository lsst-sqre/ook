"""A service that gets documentation project metadata from the LTD v1 API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from httpx import AsyncClient, Response
from safir.datetime import parse_isodatetime
from structlog.stdlib import BoundLogger

from ook.exceptions import LtdProjectNotFoundError


class LtdMetadataService:
    """A service that gets documentation project metadata from the LTD v1
    API.
    """

    def __init__(
        self, *, logger: BoundLogger, http_client: AsyncClient
    ) -> None:
        self._base = "https://keeper.lsst.codes"
        self._logger = logger
        self._http_client = http_client

    def get_product_api_url(self, product_slug: str) -> str:
        """Get the LTD API URL for a given product slug."""
        return f"{self._base}/products/{product_slug}"

    async def get_project_urls(self) -> dict:
        """Get all LTD Project URLs."""
        url = f"{self._base}/products/"
        response = await self._http_client.get(url)
        response.raise_for_status()
        return response.json()["products"]

    async def get_project(self, product_slug: str) -> dict:
        """Get the LTD project metadata for a given product slug.

        Raises
        ------
        LtdProjectNotFoundError
            Raised if the LTD API does not have the project.
        """
        url = self.get_product_api_url(product_slug)
        response = await self._http_client.get(url)
        self._raise_for_status(response, product_slug)
        return response.json()

    async def get_edition(
        self, product_slug: str, edition_slug: str = "main"
    ) -> dict:
        """Get the LTD edition metadata for a given product and edition.

        Raises
        ------
        LtdProjectNotFoundError
            Raised if the LTD API does not have the project or one of its
            editions.
        RuntimeError
            Raised if the project does not have an edition with that slug.
        """
        editions_url = f"{self._base}/products/{product_slug}/editions/"
        response = await self._http_client.get(editions_url)
        self._raise_for_status(response, product_slug)
        editions = response.json()
        for edition_url in editions["editions"]:
            response = await self._http_client.get(edition_url)
            self._raise_for_status(response, product_slug)
            edition = response.json()
            if edition["slug"] == edition_slug:
                return edition
        raise RuntimeError(
            f"Could not find edition {edition_slug} for product {product_slug}"
        )

    async def iter_updated_projects(
        self, since: datetime, edition_slug: str = "main"
    ) -> AsyncIterator[str]:
        """Get all LTD projects that have been updated since a given date.

        Parameters
        ----------
        since
            The date to compare against.
        edition_slug
            The slug of the edition. ``main`` is the default edition.

        Yields
        ------
        str
            The slug of the project that was updated recently.
        """
        for project_url in await self.get_project_urls():
            # The slug is the last component of the API URL
            project_slug = project_url.rstrip("/").split("/")[-1]
            try:
                r = await self._http_client.get(project_url)
                self._raise_for_status(r, project_slug)
                project = r.json()
                project_slug = project["slug"]
                edition = await self.get_edition(
                    product_slug=project_slug, edition_slug=edition_slug
                )
            except LtdProjectNotFoundError as e:
                self._logger.warning(
                    "Skipping LTD project that could not be found",
                    slug=e.product_slug,
                    url=e.url,
                )
                continue
            except RuntimeError:
                continue
            try:
                date_rebuilt = parse_isodatetime(edition["date_rebuilt"])
            except ValueError:
                continue
            if date_rebuilt >= since:
                yield project_slug

    @staticmethod
    def _raise_for_status(response: Response, product_slug: str) -> None:
        """Raise for an error response, mapping a 404 to
        `~ook.exceptions.LtdProjectNotFoundError`.
        """
        if response.status_code == 404:
            raise LtdProjectNotFoundError(
                product_slug=product_slug, url=str(response.request.url)
            )
        response.raise_for_status()
