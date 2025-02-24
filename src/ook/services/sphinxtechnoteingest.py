"""A service for ingesting Sphinx technote (original format) documents hosted
on LSST the Docs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

import yaml
from httpx import AsyncClient
from structlog.stdlib import BoundLogger

from ook.domain.algoliarecord import (
    DocumentRecord,
    format_timestamp,
    format_utc_datetime,
    generate_surrogate_key,
)
from ook.domain.ltdsphinxtechnote import ReducedLtdSphinxTechnote
from ook.domain.sphinxutils import SphinxSection

from .algoliadocindex import AlgoliaDocIndexService
from .githubmetadata import GitHubMetadataService


class SphinxTechnoteIngestService:
    """A service for ingesting the original generation of Rubin Sphinx technote
    documents hosted on LSST the Docs.
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

    async def ingest(
        self, *, published_url: str, project_url: str, edition_url: str
    ) -> None:
        """Ingest a Sphinx technote.

        Parameters
        ----------
        published_url
            The published URL of the Sphinx technote.
        project_url
            The API URL of the LTD project resource.
        edition_url
            The API URL of the LTD edition resource.
        """
        html_content = await self._download_html(published_url)

        project_response = await self._http_client.get(project_url)
        project_response.raise_for_status()
        project_resource = project_response.json()

        edition_response = await self._http_client.get(edition_url)
        edition_response.raise_for_status()
        edition_resource = edition_response.json()

        try:
            git_ref = edition_resource["tracked_refs"][0]
        except (KeyError, IndexError):
            git_ref = "main"

        repo_url = project_resource["doc_repo"]

        technote_metadata = await self._get_metadata_yaml(repo_url, git_ref)

        try:
            reduced_technote = ReducedLtdSphinxTechnote(
                html_source=html_content,
                url=published_url,
                metadata=technote_metadata,
                logger=self._logger,
            )
        except Exception:
            self._logger.exception("Failure making ReducedLtdSphinxTechnote")
            raise

        # Get metadata from GitHub if available
        if reduced_technote.github_url:
            (
                repo_owner,
                repo_name,
            ) = self._github_service.parse_repo_from_github_url(
                reduced_technote.github_url
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
            reduced_technote, creation_date=creation_date
        )

    async def _download_html(self, published_url: str) -> str:
        """Download the HTML content of a Sphinx technote."""
        response = await self._http_client.get(published_url)
        response.raise_for_status()
        return response.text

    async def _get_metadata_yaml(self, repo_url: str, git_ref: str) -> dict:
        """Get the metadata.yaml file from a Sphinx technote repository."""
        owner, name = self._github_service.parse_repo_from_github_url(repo_url)
        raw_url = self._github_service.format_raw_content_url(
            owner=owner, repo=name, path="metadata.yaml", git_ref=git_ref
        )
        response = await self._http_client.get(raw_url)
        response.raise_for_status()
        return yaml.safe_load(StringIO(response.text))

    async def save_to_algolia(
        self,
        reduced_document: ReducedLtdSphinxTechnote,
        creation_date: datetime | None = None,
    ) -> None:
        """Save a reduced Sphinx technote document to Algolia."""
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
        reduced_technote: ReducedLtdSphinxTechnote,
        creation_date: datetime | None = None,
    ) -> list[DocumentRecord]:
        """Create Algolia records from a reduced Sphinx technote document."""
        surrogate_key = generate_surrogate_key()

        records = [
            self._create_record(
                section=s,
                technote=reduced_technote,
                surrogate_key=surrogate_key,
                creation_date=creation_date,
            )
            for s in reduced_technote.sections
        ]

        description_section = SphinxSection(
            url=reduced_technote.url,
            headers=[reduced_technote.h1],
            content=reduced_technote.description,
        )
        records.append(
            self._create_record(
                section=description_section,
                technote=reduced_technote,
                surrogate_key=surrogate_key,
                creation_date=creation_date,
            )
        )

        return records

    def _create_record(
        self,
        section: SphinxSection,
        technote: ReducedLtdSphinxTechnote,
        surrogate_key: str,
        creation_date: datetime | None = None,
    ) -> DocumentRecord:
        """Create an Algolia record from a Sphinx section."""
        object_id = DocumentRecord.generate_object_id(
            url=section.url, headers=section.headers
        )

        record_args = {
            "object_id": object_id,
            "surrogate_key": surrogate_key,
            "source_update_time": format_utc_datetime(technote.timestamp),
            "source_update_timestamp": format_timestamp(technote.timestamp),
            "source_creation_timestamp": (
                format_timestamp(creation_date) if creation_date else None
            ),
            "record_update_time": format_utc_datetime(datetime.now(tz=UTC)),
            "url": section.url,
            "base_url": technote.url,
            "content": section.content,
            "importance": section.header_level,
            "content_categories_lvl0": "Documents",
            "content_categories_lvl1": (
                f"Documents > {technote.series.upper()}"
            ),
            "content_type": technote.content_type.value,
            "description": technote.description,
            "handle": technote.handle,
            "number": technote.number,
            "series": technote.series,
            "author_names": technote.author_names,
            "github_repo_url": technote.github_url,
        }
        for i, header in enumerate(section.headers):
            record_args[f"h{i+1}"] = header

        return DocumentRecord.model_validate(record_args)
