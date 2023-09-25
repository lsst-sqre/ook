"""Technote ingest service (for technote.lsst.io documents)."""

from __future__ import annotations

from httpx import AsyncClient
from structlog.stdlib import BoundLogger

from ..domain.ltdtechnote import LtdTechnote
from .algoliadocindex import AlgoliaDocIndexService


class TechnoteIngestService:
    """A service for ingesting technote.lsst.io documents."""

    def __init__(
        self,
        *,
        http_client: AsyncClient,
        logger: BoundLogger,
        algolia_service: AlgoliaDocIndexService,
    ) -> None:
        self._http_client = http_client
        self._logger = logger
        self._algolia_service = algolia_service

    async def ingest(
        self, *, published_url: str, project_url: str, edition_url: str
    ) -> None:
        """Ingest a technote.lsst.io document.

        Parameters
        ----------
        published_url
            The published URL of the technote.
        project_url
            The API URL of the LTD project resource.
        edition_url
            The API URL of the LTD edition resource.
        """
        html_content = await self._download_html(published_url)
        technote = LtdTechnote(html_content)
        records = technote.make_algolia_records()
        await self._algolia_service.save_document_records(records)
        self._logger.info(
            "ingested technote",
            technote=technote,
        )

    async def _download_html(self, url: str) -> str:
        """Download the HTML content of a technote."""
        r = await self._http_client.get(url)
        r.raise_for_status()
        return r.text
