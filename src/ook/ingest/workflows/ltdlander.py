"""Ingest workflow for the LTD_LANDER_JSONLD content type."""

from __future__ import annotations

import asyncio
import datetime
from typing import TYPE_CHECKING, Any, Dict

from algoliasearch.responses import MultipleResponse

from ook.ingest.algolia.expiration import delete_old_records
from ook.ingest.algolia.records import (
    DocumentRecord,
    format_timestamp,
    format_utc_datetime,
    generate_object_id,
    generate_surrogate_key,
)
from ook.ingest.reducers.ltdlander import (
    ContentChunk,
    ReducedLtdLanderDocument,
)
from ook.utils import get_json_data

if TYPE_CHECKING:
    from aiohttp import web
    from structlog._config import BoundLoggerLazyProxy

__all__ = ["ingest_ltd_lander_jsonld_document"]


async def ingest_ltd_lander_jsonld_document(
    *,
    app: web.Application,
    logger: BoundLoggerLazyProxy,
    url_ingest_message: Dict[str, Any],
) -> None:
    """Run the Algolia ingest of a LTD_LANDER_JSONLD content type.

    Parameters
    ----------
    app : `aiohttp.web.Application`
        The app.
    logger
        A structlog logger that is bound with context about the Kafka message.
    url_ingest_message : `dict`
        The deserialized value of the Kafka message.
    """
    logger = logger.bind(
        content_url=url_ingest_message["url"],
        content_type=url_ingest_message["content_type"],
    )
    logger.info("Starting LTD_LANDER_JSONLD ingest")

    http_session = app["safir/http_session"]

    edition_data = await get_json_data(
        url=url_ingest_message["edition"]["url"],
        logger=logger,
        http_session=http_session,
    )

    published_url = edition_data["published_url"]
    jsonld_name = "metadata.jsonld"
    if published_url.endswith("/"):
        jsonld_url = f"{published_url}{jsonld_name}"
    else:
        jsonld_url = f"{published_url}/{jsonld_name}"

    try:
        metadata = await get_json_data(
            url=jsonld_url,
            logger=logger,
            http_session=http_session,
            # by-pass aiohttp's encoding check; the jsonld files do not have
            # correct CONTENT-TYPE headers.
            encoding="utf-8",
            content_type=None,
        )
    except Exception:
        logger.exception(
            "Failure getting metadata.jsonld", jsonld_url=jsonld_url
        )
        raise

    try:
        reduced_document = ReducedLtdLanderDocument(
            url=published_url, metadata=metadata, logger=logger
        )
    except Exception:
        logger.exception("Failed to build record")
        raise

    surrogate_key = generate_surrogate_key()

    logger.debug(
        "Reduced LTD Lander Document", chunks=len(reduced_document.chunks)
    )

    try:
        records = [
            create_record(
                chunk=s,
                document=reduced_document,
                surrogate_key=surrogate_key,
            )
            for s in reduced_document.chunks
        ]

        description_chunk = ContentChunk(
            headers=[reduced_document.h1],
            content=reduced_document.description,
        )
        records.append(
            create_record(
                chunk=description_chunk,
                document=reduced_document,
                surrogate_key=surrogate_key,
            )
        )
    except Exception:
        logger.exception("Failed to build records")
        raise

    logger.info("Finished building records")

    if app["ook/algolia_search"] is not None:
        try:
            client = app["ook/algolia_search"]
            index = client.init_index(
                app["safir/config"].algolia_document_index_name
            )
        except Exception:
            logger.exception(
                "Error initializing Algolia index",
                index_name=app["safir/config"].algolia_document_index_name,
            )
            raise

        tasks = [index.save_object_async(record) for record in records]
        results = await asyncio.gather(*tasks)
        MultipleResponse(results).wait()

        logger.info("Finished uploading to Algolia")

        await delete_old_records(
            index=index,
            base_url=records[0]["baseUrl"],
            surrogate_key=surrogate_key,
            logger=logger,
        )


def create_record(
    *,
    document: ReducedLtdLanderDocument,
    chunk: ContentChunk,
    surrogate_key: str,
    validate: bool = True,
) -> Dict[str, Any]:
    """Create a JSON-serializable record for the Algolia index."""
    object_id = generate_object_id(
        url=document.url,
        headers=chunk.headers,
        paragraph_index=chunk.paragraph,
    )
    record = {
        "objectID": object_id,
        "surrogateKey": surrogate_key,
        "sourceUpdateTime": format_utc_datetime(document.timestamp),
        "sourceUpdateTimestamp": format_timestamp(document.timestamp),
        "recordUpdateTime": format_utc_datetime(datetime.datetime.utcnow()),
        "url": document.url,
        "baseUrl": document.url,
        "content": chunk.content,
        "importance": chunk.header_level,
        "contentCategories.lvl0": "Documents",
        "contentCategories.lvl1": (f"Documents > {document.series.upper()}"),
        "contentType": document.content_type.value,
        "description": document.description,
        "handle": document.handle,
        "number": document.number,
        "series": document.series,
        "authorNames": document.author_names,
        "pIndex": chunk.paragraph,
    }
    for i, header in enumerate(chunk.headers):
        record[f"h{i+1}"] = header
    if document.github_url is not None:
        record["githubRepoUrl"] = document.github_url

    if validate:
        DocumentRecord.parse_obj(record)

    return record
