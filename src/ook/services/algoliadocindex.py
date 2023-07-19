"""Service for working with Algolia indexes."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from algoliasearch.search_index import SearchIndex
from pydantic import HttpUrl
from structlog.stdlib import BoundLogger

from ..domain.algoliarecord import DocumentRecord, MinimalDocumentModel

__all__ = ["AlgoliaDocIndexService"]


class AlgoliaDocIndexService:
    """An Ook service that operates on the Algolia document index.

    Parameters
    ----------
    index
        The Algolia document index.
    logger
        The logger.
    """

    def __init__(self, index: SearchIndex, logger: BoundLogger) -> None:
        self._index = index
        self._logger = logger

    async def save_document_records(
        self, records: Sequence[DocumentRecord]
    ) -> None:
        """Save document records to the Algolia index and delete old records.

        Parameters
        ----------
        doc_records
            The document records to save. These records can be from different
            base URLs.

        Raises
        ------
        RuntimeError
            Raised if records for a given base URL have different surrogate
            keys. This is an indication that the surrogate keys are not
            being generated correctly.
        """
        # Partition the records by base URL since each URL has a different
        # surrogate key, and thus is old records need to be deleted separately.
        partitioned_records: defaultdict[
            HttpUrl, list[DocumentRecord]
        ] = defaultdict(list)
        for record in records:
            partitioned_records[record.base_url].append(record)
        for base_url, url_records in partitioned_records.items():
            surrogate_keys = {record.surrogate_key for record in url_records}
            if len(surrogate_keys) > 1:
                raise RuntimeError(
                    f"Multiple surrogate keys for base URL {base_url}: "
                    f"{surrogate_keys}"
                )
            surrogate_key = surrogate_keys.pop()
            record_objs = [
                record.export_for_algolia() for record in url_records
            ]
            await self._index.save_objects_async(record_objs)
            await self.delete_old_records(base_url, surrogate_key)

    async def save_doc_stub(self, doc: MinimalDocumentModel) -> None:
        """Add a manually-generated record to the document index."""
        record = doc.make_algolia_record().export_for_algolia()
        await self._index.save_objects_async([record])
        await self.delete_old_records(
            base_url=record["baseUrl"],
            surrogate_key=record["surrogateKey"],
        )
        await self.delete_old_records(
            record["baseUrl"], record["surrogateKey"]
        )

    async def find_old_records(
        self, base_url: str, surrogate_key: str
    ) -> list[dict[str, Any]]:
        """Find all records for a URL that don't match the given surrogate
        key.
        """
        filters = (
            f"baseUrl:{self.escape_facet_value(base_url)}"
            " AND NOT "
            f"surrogateKey:{self.escape_facet_value(surrogate_key)}"
        )

        records: list[dict[str, Any]] = []
        async for result in self._index.browse_objects_async(
            {
                "filters": filters,
                "attributesToRetrieve": ["baseUrl", "surrogateKey"],
                "attributesToHighlight": [],
            }
        ):
            records.append(result)
        return records

    async def delete_old_records(
        self, base_url: str, surrogate_key: str
    ) -> None:
        """Delete all records for a URL that don't match the given surrogate
        key.
        """
        records = await self.find_old_records(base_url, surrogate_key)
        object_ids = [record["objectID"] for record in records]
        self._logger.debug(
            "Collected old objectIDs for deletion",
            base_url=base_url,
            object_id_count=len(object_ids),
        )
        await self._index.delete_objects_async(object_ids)

    @staticmethod
    def escape_facet_value(value: str) -> str:
        """Escape a facet value for use in Algolia filters."""
        value = value.replace('"', r"\"").replace("'", r"\'")
        return f'"{value}"'
