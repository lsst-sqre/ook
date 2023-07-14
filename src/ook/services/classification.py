"""Service for classifying an input source."""

from __future__ import annotations

import re

from httpx import AsyncClient
from structlog.stdlib import BoundLogger

from ook.domain.algoliarecord import DocumentSourceType

from .githubmetadata import GitHubMetadataService

__all__ = ["ClassificationService"]


DOC_SLUG_PATTERN = re.compile(r"^[a-z]+-[0-9]+$")
"""Regular expression pattern for a LTD product slug that matches a document.

For example, ``sqr-000`` or ``ldm-151``.
"""


class ClassificationService:
    """An Ook service that classifies input URLs and plans processing
    by creating queued ingest tasks.

    Parameters
    ----------
    http_client
        The HTTP client.
    logger
        The logger.
    """

    def __init__(
        self,
        *,
        http_client: AsyncClient,
        github_service: GitHubMetadataService,
        logger: BoundLogger,
    ) -> None:
        self._http_client = http_client
        self._logger = logger
        self._gh_service = github_service

    async def classify_ltd_site(
        self, *, product_slug: str, published_url: str
    ) -> DocumentSourceType:
        """Classify the type of an LSST the Docs-based site.

        Parameters
        ----------
        product_slug
            The LTD Product resource's slug.
        published_url
            The published URL of the site (usually the edition's published
            URL).

        Returns
        -------
        ContentType
            The known site type.
        """
        if self.is_document_handle(product_slug):
            # Either a lander-based site or a sphinx technote
            if await self.has_jsonld_metadata(published_url=published_url):
                return DocumentSourceType.LTD_LANDER_JSONLD
            elif await self.has_metadata_yaml(product_slug=product_slug):
                return DocumentSourceType.LTD_SPHINX_TECHNOTE
            else:
                return DocumentSourceType.LTD_GENERIC
        else:
            return DocumentSourceType.LTD_GENERIC

    def is_document_handle(self, product_slug: str) -> bool:
        """Test if a LSST the Docs product slug belongs to a Rubin Observatory
        document (as opposed to a general documentation site).

        Parameters
        ----------
        product_slug : `str`
            The "slug" of the LTD Product resource (which is the subdomain that
            the document is served from. For example, ``"sqr-000"`` is the slug
            for the https://sqr-001.lsst.io site of the SQR-000 technote.

        Returns
        -------
        bool
            `True` if the slug indicates a document or `False` otherwise.
        """
        return bool(DOC_SLUG_PATTERN.match(product_slug))

    async def has_jsonld_metadata(self, *, published_url: str) -> bool:
        """Test if an LSST the Docs site has a ``metadata.jsonld`` path,
        indicating it is a Lander-based document.

        Parameters
        ----------
        published_url : `str`
            The published URL of the site (usually the edition's published
            URL).

        Returns
        -------
        bool
            `True` if the ``metadata.jsonld`` path exists or `False` otherwise.
        """
        jsonld_name = "metadata.jsonld"
        if published_url.endswith("/"):
            jsonld_url = f"{published_url}{jsonld_name}"
        else:
            jsonld_url = f"{published_url}/{jsonld_name}"

        response = await self._http_client.head(jsonld_url)
        return response.status_code == 200

    async def has_metadata_yaml(self, *, product_slug: str) -> bool:
        """Test if an LSST the Docs site has a ``metadata.yaml`` file in its
        Git repository, indicating its a Sphinx-based technote.
        """
        response = await self._http_client.get(
            f"https://keeper.lsst.codes/products/{product_slug}"
        )
        product_data = response.json()

        default_git_refs = ["main", "master"]
        for git_ref in default_git_refs:
            if await self._has_metadata_yaml(
                repo_url=product_data["doc_repo"],
                git_ref=git_ref,
            ):
                return True
        return False

    async def _has_metadata_yaml(
        self,
        *,
        repo_url: str,
        git_ref: str,
    ) -> bool:
        owner, repo = self._gh_service.parse_repo_from_github_url(repo_url)
        raw_url = self._gh_service.format_raw_content_url(
            owner=owner, repo=repo, git_ref=git_ref, path="metadata.yaml"
        )
        response = await self._http_client.get(raw_url)
        if response.status_code != 200:
            return False
        return True
