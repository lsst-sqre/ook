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
        # Partition the records by base URL since each URL is replaced as a
        # unit: its stale records are deleted separately.
        partitioned_records: defaultdict[HttpUrl, list[DocumentRecord]] = (
            defaultdict(list)
        )
        for record in records:
            partitioned_records[record.base_url].append(record)
        for base_url, url_records in partitioned_records.items():
            surrogate_keys = {record.surrogate_key for record in url_records}
            if len(surrogate_keys) > 1:
                raise RuntimeError(
                    f"Multiple surrogate keys for base URL {base_url}: "
                    f"{surrogate_keys}"
                )
            record_objs = [
                record.export_for_algolia() for record in url_records
            ]
            await self.replace_records(str(base_url), record_objs)

    async def save_doc_stub(self, doc: MinimalDocumentModel) -> None:
        """Add a manually-generated record to the document index."""
        record = doc.make_algolia_record().export_for_algolia()
        await self.replace_records(str(record["baseUrl"]), [record])

    async def replace_records(
        self, base_url: str, records: list[dict[str, Any]]
    ) -> None:
        """Replace the index's records for a base URL with new records.

        The object IDs already indexed for the base URL are collected *before*
        the new records are saved, and only the IDs absent from the new set
        are deleted afterwards. Algolia applies writes asynchronously and in
        order, so a browse issued right after a save may still see the
        previous records. Because object IDs are derived from the URL and
        section headings, those previous records share IDs with the fresh
        ones; deleting them based on that stale view would wipe the records
        that were just saved. Deciding what to delete from the pre-save
        snapshot never collides with the new IDs.

        Parameters
        ----------
        base_url
            The base URL whose records are being replaced.
        records
            The new records, exported for Algolia (with ``objectID`` and
            ``baseUrl`` keys).
        """
        existing_ids = await self.find_object_ids(base_url)
        await self._index.save_objects_async(records)
        new_ids = {record["objectID"] for record in records}
        stale_ids = sorted(existing_ids - new_ids)
        if stale_ids:
            await self._index.delete_objects_async(stale_ids)
        self._logger.debug(
            "Replaced document records",
            base_url=base_url,
            saved_count=len(records),
            deleted_count=len(stale_ids),
        )

    async def find_object_ids(self, base_url: str) -> set[str]:
        """Find the object IDs of all records indexed for a base URL."""
        settings = {
            "filters": f"baseUrl:{self.escape_facet_value(base_url)}",
            "attributesToRetrieve": ["objectID"],
            "attributesToHighlight": [],
        }

        # Ideally we want to use browse_objects_async here, but I think there's
        # a bug:
        # https://github.com/algolia/algoliasearch-client-python/issues/559
        return {
            record["objectID"]
            for record in self._index.browse_objects(settings)
        }

    @staticmethod
    def escape_facet_value(value: str) -> str:
        """Escape a facet value for use in Algolia filters."""
        value = value.replace('"', r"\"").replace("'", r"\'")
        return f'"{value}"'
