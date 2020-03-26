"""Process edition.updated events."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from aiohttp import web

from ook.classification import classify_ltd_site

if TYPE_CHECKING:
    from structlog._config import BoundLoggerLazyProxy

__all__ = ["process_edition_updated"]


async def process_edition_updated(
    *,
    app: web.Application,
    logger: BoundLoggerLazyProxy,
    message: Dict[str, Any],
) -> None:
    """Process an ``edition.updated`` event from LTD Events.

    Parameters
    ----------
    app : `aiohttp.web.Application`
        The app.
    logger
        A structlog logger that is bound with context about the Kafka message.
    message : `dict`
        The deserialized value of the Kafka message.
    """
    logger.info("In process_edition_updated")

    content_type = await classify_ltd_site(
        http_session=app["safir/http_session"],
        product_slug=message["product"]["slug"],
        published_url=message["edition"]["published_url"],
    )
    logger.info("Classified LTD site", content_type=content_type)
