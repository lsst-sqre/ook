"""Ingest workflow for the LTD_SPHINX_TECHNOTE content type."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any, Dict
from urllib.parse import urlparse

import yaml
from algoliasearch.responses import MultipleResponse

from ook.ingest.algolia.records import LtdSphinxTechnoteSectionRecord
from ook.ingest.reducers.ltdsphinxtechnote import ReducedLtdSphinxTechnote
from ook.ingest.reducers.sphinxutils import SphinxSection

if TYPE_CHECKING:
    from aiohttp import web, ClientSession
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
        )
    except Exception:
        logger.exception("Failure making ReducedLtdSphinxTechnote")
        raise

    surrogate_key = uuid.uuid4().hex

    try:
        records = [
            LtdSphinxTechnoteSectionRecord(
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
            LtdSphinxTechnoteSectionRecord(
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

        tasks = [index.save_object_async(record.data) for record in records]
        results = await asyncio.gather(*tasks)
        MultipleResponse(results).wait()

        logger.info("Finished uploading to Algolia")


async def get_html_content(
    *, url: str, http_session: ClientSession, logger: BoundLoggerLazyProxy
) -> str:
    html_content_response = await http_session.get(url)
    if html_content_response.status != 200:
        raise RuntimeError(
            f"Could not download {url}."
            f"Got status {html_content_response.status}."
        )
    return await html_content_response.text()


async def get_json_data(
    *, url: str, http_session: ClientSession, logger: BoundLoggerLazyProxy
) -> Dict[str, Any]:
    response = await http_session.get(url)
    if response.status != 200:
        raise RuntimeError(
            f"Could not download {url}." f"Got status {response.status}."
        )
    return await response.json()


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


def make_raw_github_url(
    *, repo_path: str, git_ref: str, file_path: str
) -> str:
    if file_path.startswith("/"):
        file_path = file_path.lstrip("/")
    if repo_path.startswith("/"):
        repo_path = repo_path.lstrip("/")
    if repo_path.endswith("/"):
        repo_path = repo_path.rstrip("/")

    return (
        f"https://raw.githubusercontent.com/{repo_path}/{git_ref}/{file_path}"
    )
