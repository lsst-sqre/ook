"""Service for auditing the Algolia indices for completeness."""

from __future__ import annotations

import re
from dataclasses import dataclass

from algoliasearch.search_client import SearchClient
from httpx import AsyncClient
from structlog.stdlib import BoundLogger

from ..config import config
from .classification import ClassificationService

# Python regular expression pattern that matches an LTD document slug such as
# "sqr-000".
LTD_SLUG_PATTERN = re.compile(r"^[a-z]+-[0-9]+$")


@dataclass
class LtdDocument:
    """Information about a document registered in LTD."""

    published_url: str
    """The base URL where the document is published."""

    slug: str
    """The LTD slug for the document."""

    @property
    def handle(self) -> str:
        """The handle for the document in Algolia."""
        return self.slug.upper()

    def __lt__(self, other: LtdDocument) -> bool:
        """Sort documents by their handle."""
        return self.handle < other.handle

    def __le__(self, other: LtdDocument) -> bool:
        """Sort documents by their handle."""
        return self.handle <= other.handle

    def __gt__(self, other: LtdDocument) -> bool:
        """Sort documents by their handle."""
        return self.handle > other.handle

    def __ge__(self, other: LtdDocument) -> bool:
        """Sort documents by their handle."""
        return self.handle >= other.handle


class AlgoliaAuditService:
    """A service for auditing the Algolia indices for completeness."""

    def __init__(
        self,
        *,
        http_client: AsyncClient,
        logger: BoundLogger,
        algolia_search_client: SearchClient,
        classification_service: ClassificationService,
    ) -> None:
        """Initialize the service."""
        self._http_client = http_client
        self._search_client = algolia_search_client
        self._classifier = classification_service
        self._logger = logger

    async def audit_missing_documents(
        self, *, ingest_missing: bool = False
    ) -> list[LtdDocument]:
        """Audit the Algolia indices for completeness of missing documents.

        A document is considered "missing" if it is registered in the LTD API,
        but its handle is not in the Algolia index.

        This audit only tests documents, not documentation projects (user
        guides).

        Returns
        -------
        list
            A list of missing documents.
        """
        expected_ltd_docs = await self._get_ltd_documents()
        expected_ltd_docs.sort()

        missing_docs: list[LtdDocument] = []

        doc_index = self._search_client.init_index(
            config.algolia_document_index_name
        )
        for expected_ltd_doc in expected_ltd_docs:
            result = doc_index.search(
                expected_ltd_doc.slug.upper(),
                {"restrictSearchableAttributes": "handle"},
            )
            if result["nbHits"] == 0:
                self._logger.warning(
                    "Document not found in Algolia index",
                    handle=expected_ltd_doc.slug.upper(),
                    published_url=expected_ltd_doc.published_url,
                )
                missing_docs.append(expected_ltd_doc)
        missing_docs.sort()

        self._logger.info(
            "Audit complete.",
            found=len(expected_ltd_docs) - len(missing_docs),
            missing=len(missing_docs),
        )

        if ingest_missing and len(missing_docs) > 0:
            reingest_count = 0
            for doc in missing_docs:
                try:
                    await self._classifier.queue_ingest_for_ltd_product_slug(
                        product_slug=doc.slug, edition_slug="main"
                    )
                    reingest_count += 1
                except Exception:
                    self._logger.exception(
                        "Failed to queue ingest for missing document",
                        handle=doc.slug.upper(),
                        published_url=doc.published_url,
                    )
            self._logger.info(
                "Queued ingest for missing documents",
                queued=reingest_count,
                failed=len(missing_docs) - reingest_count,
            )

        return missing_docs

    async def _get_ltd_documents(self) -> list[LtdDocument]:
        """Get a list of documents registered in LTD."""
        r = await self._http_client.get("https://keeper.lsst.codes/products/")
        products = r.json()

        documents: list[LtdDocument] = []

        for product_api_url in products["products"]:
            slug = product_api_url.split("/")[-1]
            if LTD_SLUG_PATTERN.match(slug) is None:
                continue
            series = slug.upper().split("-")[0]
            if series in ("TEST", "TESTN", "TESTDOC", "TESTR"):
                # Skip known test document series before requesting their
                # metadata.
                continue

            r = await self._http_client.get(product_api_url)
            product = r.json()

            if product["doc_repo"].startswith(
                "https://github.com/lsst-sqre-testing/"
            ):
                # Skip known test documents.
                continue

            document = LtdDocument(
                published_url=product["published_url"],
                slug=product["slug"],
            )
            documents.append(document)
        return documents
