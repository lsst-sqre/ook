"""Ingest workflow for the LTD_SPHINX_TECHNOTE content type."""

from __future__ import annotations

import asyncio
import datetime
import json
from typing import TYPE_CHECKING, Any, Dict
from urllib.parse import urlparse

import yaml
from algoliasearch.responses import MultipleResponse

from ook.ingest.algolia.expiration import delete_old_records
from ook.ingest.algolia.records import (
    DocumentRecord,
    format_timestamp,
    format_utc_datetime,
    generate_object_id,
    generate_surrogate_key,
)
from ook.ingest.reducers.ltdsphinxtechnote import ReducedLtdSphinxTechnote
from ook.ingest.reducers.sphinxutils import SphinxSection
from ook.utils import get_html_content, get_json_data, make_raw_github_url

if TYPE_CHECKING:
    from aiohttp import ClientSession, web
    from structlog._config import BoundLoggerLazyProxy

__all__ = ["ingest_ltd_sphinx_technote"]


async def ingest_ltd_sphinx_technote(
    *,
    app: web.Application,
    logger: BoundLoggerLazyProxy,
    url_ingest_message: Dict[str, Any],
) -> None:
    """Run the Algolia ingest of a LTD_SPHINX_TECHNOTE content type.

    Parameters
    ----------
    app : `aiohttp.web.Application`
        The app.
    logger
        A structlog logger that is bound with context about the Kafka message.
    message : `dict`
        The deserialized value of the Kafka message.
    """
    logger = logger.bind(
        content_url=url_ingest_message["url"],
        content_type=url_ingest_message["content_type"],
    )
    logger.info("Starting LTD_SPHINX_TECHNOTE ingest")

    http_session = app["safir/http_session"]

    html_content = await get_html_content(
        url=url_ingest_message["url"], logger=logger, http_session=http_session
    )

    product_data = await get_json_data(
        url=url_ingest_message["product"]["url"],
        logger=logger,
        http_session=http_session,
    )

    edition_data = await get_json_data(
        url=url_ingest_message["edition"]["url"],
        logger=logger,
        http_session=http_session,
    )

    try:
        git_ref = edition_data["tracked_refs"][0]
    except Exception:
        git_ref = "master"

    try:
        metadata = await get_metadata(
            repo_url=product_data["doc_repo"],
            git_ref=git_ref,
            http_session=http_session,
            logger=logger,
        )
    except Exception:
        logger.exception("Failed to download metadata.yaml")
        raise

    try:
        reduced_technote = ReducedLtdSphinxTechnote(
            html_source=html_content,
            url=url_ingest_message["url"],
            metadata=metadata,
            logger=logger,
        )
    except Exception:
        logger.exception("Failure making ReducedLtdSphinxTechnote")
        raise

    surrogate_key = generate_surrogate_key()

    try:
        records = [
            create_record(
                section=s,
                technote=reduced_technote,
                surrogate_key=surrogate_key,
            )
            for s in reduced_technote.sections
        ]

        description_section = SphinxSection(
            url=reduced_technote.url,
            headers=[reduced_technote.h1],
            content=reduced_technote.description,
        )
        records.append(
            create_record(
                section=description_section,
                technote=reduced_technote,
                surrogate_key=surrogate_key,
            )
        )
    except Exception:
        logger.exception(
            "Failed to build LtdSphinxTechnoteSectionRecord records"
        )
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
        try:
            results = await asyncio.gather(*tasks)
            MultipleResponse(results).wait()
        except Exception:
            logger.error("Got algoliasearch request error")
            for record in records:
                logger.debug(json.dumps(record, indent=2, sort_keys=True))

        logger.info("Finished uploading to Algolia")

        await delete_old_records(
            index=index,
            base_url=records[0]["baseUrl"],
            surrogate_key=surrogate_key,
            logger=logger,
        )


async def get_metadata(
    *,
    repo_url: str,
    git_ref: str,
    http_session: ClientSession,
    logger: BoundLoggerLazyProxy,
) -> Dict[str, Any]:
    if repo_url.endswith("/"):
        repo_url = repo_url.rstrip("/")
    if repo_url.endswith(".git"):
        repo_url = repo_url[: -len(".git")]

    repo_url_parts = urlparse(repo_url)
    repo_path = repo_url_parts[2]

    raw_url = make_raw_github_url(
        repo_path=repo_path, git_ref=git_ref, file_path="metadata.yaml"
    )

    response = await http_session.get(raw_url)
    if response.status != 200:
        raise RuntimeError(
            f"Could not download {raw_url}. Got status {response.status}."
        )
    metadata_text = await response.text()

    metadata = yaml.safe_load(metadata_text)

    return metadata


def create_record(
    *,
    section: SphinxSection,
    technote: ReducedLtdSphinxTechnote,
    surrogate_key: str,
    validate: bool = True,
) -> Dict[str, Any]:
    """Create a JSON-serializable record for the Algolia index."""
    object_id = generate_object_id(url=section.url, headers=section.headers)
    record = {
        "objectID": object_id,
        "surrogateKey": surrogate_key,
        "sourceUpdateTime": format_utc_datetime(technote.timestamp),
        "sourceUpdateTimestamp": format_timestamp(technote.timestamp),
        "recordUpdateTime": format_utc_datetime(datetime.datetime.utcnow()),
        "url": section.url,
        "baseUrl": technote.url,
        "content": section.content,
        "importance": section.header_level,
        "contentCategories.lvl0": "Documents",
        "contentCategories.lvl1": (f"Documents > {technote.series.upper()}"),
        "contentType": technote.content_type.value,
        "description": technote.description,
        "handle": technote.handle,
        "number": technote.number,
        "series": technote.series,
        "authorNames": technote.author_names,
    }
    for i, header in enumerate(section.headers):
        record[f"h{i+1}"] = header
    if technote.github_url is not None:
        record["githubRepoUrl"] = technote.github_url

    if validate:
        DocumentRecord.parse_obj(record)

    return record
