"""Service for classifying an input source."""

from __future__ import annotations

import re
from typing import Any, dict
from urllib.parse import urlparse

import yaml
from httpx import AsyncClient
from structlog import BoundLogger

from ook.domain.algoliarecord import DocumentSourceType
from ook.utils import make_raw_github_url

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
        self, *, http_client: AsyncClient, logger: BoundLogger
    ) -> None:
        self._http_client = http_client
        self._logger = logger

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

        try:
            await self._get_metadata_yaml(
                repo_url=product_data["doc_repo"],
                git_ref="main",
            )
        except RuntimeError:
            try:
                await self._get_metadata_yaml(
                    repo_url=product_data["doc_repo"],
                    git_ref="master",
                )
            except RuntimeError:
                return False

        return True

    async def _get_metadata_yaml(
        self,
        *,
        repo_url: str,
        git_ref: str,
    ) -> dict[str, Any]:
        # Note this is copied from ook.ingest.workflows.ltdsphinxtechnote.
        # We'll want to refactor this code to avoid circular dependencies.
        if repo_url.endswith("/"):
            repo_url = repo_url.rstrip("/")
        if repo_url.endswith(".git"):
            repo_url = repo_url[: -len(".git")]

        repo_url_parts = urlparse(repo_url)
        repo_path = repo_url_parts[2]

        raw_url = make_raw_github_url(
            repo_path=repo_path, git_ref=git_ref, file_path="metadata.yaml"
        )

        response = await self._http_client.get(raw_url)
        if response.status_code != 200:
            raise RuntimeError(
                f"Could not download {raw_url}. Got status {response.status}."
            )
        metadata_text = await response.text()

        return yaml.safe_load(metadata_text)
