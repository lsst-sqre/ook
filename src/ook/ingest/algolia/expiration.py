"""Expiration of old records in an Algolia index using a surrogate key."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List

if TYPE_CHECKING:
    from algoliasearch.search_index import SearchIndex
    from structlog.stdlib import BoundLogger

__all__ = [
    "delete_old_records",
    "search_for_old_records",
    "escape_facet_value",
]


async def delete_old_records(
    *,
    index: SearchIndex,
    base_url: str,
    surrogate_key: str,
    logger: BoundLogger,
) -> None:
    """Delete records for a given URL that do not posess the current surrogate
    key.

    Parameters
    ----------
    index
        The Algolia search index.
    base_url : `str`
        The ``baseUrl`` property of the records.
    surrogate_key : `str`
        The value of ``surrogateKey`` for the current ingest. Records for the
        ``baseUrl`` that don't have this surrogate key value are deleted.
    logger
        A structlog logging instance.
    """
    object_ids: List[str] = []
    async for record in search_for_old_records(
        index=index, base_url=base_url, surrogate_key=surrogate_key
    ):
        if record["baseUrl"] != base_url:
            logger.warning(f'baseUrl does not match: {record["baseUrl"]}')
            continue
        if record["surrogateKey"] == surrogate_key:
            logger.warning(
                f'surrogateKey matches current key: {record["surrogateKey"]}'
            )
            continue
        object_ids.append(record["objectID"])

    logger.info(
        "Collected old objectIDs for deletion",
        base_url=base_url,
        object_id_count=len(object_ids),
    )

    await index.delete_objects_async(object_ids)

    logger.info(
        "Finished deleting expired objects",
        base_url=base_url,
        object_id_count=len(object_ids),
    )


async def search_for_old_records(
    *, index: SearchIndex, base_url: str, surrogate_key: str
) -> AsyncIterator[Dict[str, Any]]:
    filters = (
        f"baseUrl:{escape_facet_value(base_url)}"
        " AND NOT "
        f"surrogateKey:{escape_facet_value(surrogate_key)}"
    )

    async for result in index.browse_objects_async(
        {
            "filters": filters,
            "attributesToRetrieve": ["baseUrl", "surrogateKey"],
            "attributesToHighlight": [],
        }
    ):
        yield result


def escape_facet_value(value: str) -> str:
    """Escape and quote a facet value for an Algolia search."""
    value = value.replace('"', r"\"").replace("'", r"\'")
    value = f'"{value}"'
    return value
