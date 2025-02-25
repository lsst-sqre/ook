"""A service for ingesting Lander v1 documents hosted on LSST the Docs."""

from __future__ import annotations

from datetime import UTC, datetime

from httpx import AsyncClient, HTTPError
from structlog.stdlib import BoundLogger

from ook.domain.algoliarecord import (
    DocumentRecord,
    format_timestamp,
    format_utc_datetime,
    generate_surrogate_key,
)
from ook.domain.ltdlander import ContentChunk, ReducedLtdLanderDocument

from .algoliadocindex import AlgoliaDocIndexService
from .githubmetadata import GitHubMetadataService


class LtdLanderJsonLdIngestService:
    """A service for ingesting Lander v1 documents hosted on LSST the Docs
    that use JSON-LD metadata.
    """

    def __init__(
        self,
        *,
        http_client: AsyncClient,
        algolia_service: AlgoliaDocIndexService,
        github_service: GitHubMetadataService,
        logger: BoundLogger,
    ) -> None:
        self._http_client = http_client
        self._algolia_service = algolia_service
        self._github_service = github_service
        self._logger = logger

    async def ingest(self, *, published_url: str) -> None:
        # Get the metadata.jsonld document from the landing page
        try:
            jsonld_metadata = await self._get_lander_jsonld_metadata(
                published_url
            )
        except HTTPError:
            self._logger.exception(
                "Failed to get JSON-LD metadata for Lander v1 document",
                published_url=published_url,
            )
            raise

        # Parse the metadata.jsonld document into section chunks
        reduced_document = ReducedLtdLanderDocument(
            url=published_url, metadata=jsonld_metadata, logger=self._logger
        )

        # Get metadata from GitHub if available
        if reduced_document.github_url:
            (
                repo_owner,
                repo_name,
            ) = self._github_service.parse_repo_from_github_url(
                reduced_document.github_url
            )
            creation_date: datetime | None = (
                await self._github_service.get_repo_first_commit_date(
                    owner=repo_owner,
                    repo=repo_name,
                )
            )
        else:
            self._logger.warning(
                "Did not capture sourceCreationTimestamp because github_url "
                "is not available."
            )
            creation_date = None

        await self.save_to_algolia(
            reduced_document, creation_date=creation_date
        )

    async def _get_lander_jsonld_metadata(self, published_url: str) -> dict:
        """Get the JSON-LD metadata for a Lander v1 document hosted on LSST
        the Docs.

        Parameters
        ----------
        published_url
            The published URL of the document.

        Returns
        -------
        dict
            The JSON-LD metadata.
        """
        jsonld_name = "metadata.jsonld"
        if published_url.endswith("/"):
            jsonld_url = f"{published_url}{jsonld_name}"
        else:
            jsonld_url = f"{published_url}/{jsonld_name}"

        response = await self._http_client.get(jsonld_url)
        response.raise_for_status()
        return response.json()

    async def save_to_algolia(
        self,
        reduced_document: ReducedLtdLanderDocument,
        creation_date: datetime | None = None,
    ) -> None:
        """Save a reduced Lander v1 document to Algolia."""
        try:
            records = self._create_records(reduced_document, creation_date)
        except Exception:
            self._logger.exception("Failed to build records")
            raise

        await self._algolia_service.save_document_records(records)

        self._logger.info(
            "Finished uploading document records",
            record_count=len(records),
            surrogate_key=records[0].surrogate_key,
        )

    def _create_records(
        self,
        reduced_document: ReducedLtdLanderDocument,
        creation_date: datetime | None = None,
    ) -> list[DocumentRecord]:
        surrogate_key = generate_surrogate_key()

        records = [
            self._create_record(
                chunk=s,
                document=reduced_document,
                surrogate_key=surrogate_key,
                creation_date=creation_date,
            )
            for s in reduced_document.chunks
        ]

        # Add an extra record that is the document's description with the H1
        # header as the title. This becomes the description shown by default
        # in the search results.
        description_chunk = ContentChunk(
            headers=[reduced_document.h1],
            content=reduced_document.description,
        )
        records.append(
            self._create_record(
                chunk=description_chunk,
                document=reduced_document,
                surrogate_key=surrogate_key,
                creation_date=creation_date,
            )
        )
        return records

    def _create_record(
        self,
        *,
        document: ReducedLtdLanderDocument,
        chunk: ContentChunk,
        surrogate_key: str,
        creation_date: datetime | None,
    ) -> DocumentRecord:
        object_id = DocumentRecord.generate_object_id(
            url=document.url,
            headers=chunk.headers,
            paragraph_index=chunk.paragraph,
        )
        record_args = {
            "object_id": object_id,
            "surrogate_key": surrogate_key,
            "source_update_time": format_utc_datetime(document.timestamp),
            "source_update_timestamp": format_timestamp(document.timestamp),
            "source_creation_timestamp": (
                format_timestamp(creation_date) if creation_date else None
            ),
            "record_update_time": format_utc_datetime(datetime.now(tz=UTC)),
            "url": document.url,
            "base_url": document.url,
            "content": chunk.content,
            "importance": chunk.header_level,
            "content_categories_lvl0": "Documents",
            "content_categories_lvl1": (
                f"Documents > {document.series.upper()}"
            ),
            "content_type": document.content_type.value,
            "description": document.description,
            "handle": document.handle,
            "number": document.number,
            "series": document.series,
            "author_names": document.author_names,
            "p_index": chunk.paragraph,
            "github_repo_url": document.github_url,
        }
        for i, header in enumerate(chunk.headers):
            record_args[f"h{i+1}"] = header

        return DocumentRecord.model_validate(record_args)
