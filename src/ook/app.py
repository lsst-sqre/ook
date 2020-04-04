"""The main application definition for ook service."""

__all__ = ["create_app"]

from typing import Any, AsyncGenerator

from aiohttp import web
from algoliasearch.search_client import SearchClient
from safir.events import (
    configure_kafka_ssl,
    init_kafka_producer,
    init_recordname_schema_manager,
)
from safir.http import init_http_session
from safir.logging import configure_logging
from safir.metadata import setup_metadata
from safir.middleware import bind_logger

from ook.config import Configuration
from ook.events.router import consume_events
from ook.handlers import init_external_routes, init_internal_routes


def create_app(**configs: Any) -> web.Application:
    """Create and configure the aiohttp.web application."""
    config = Configuration(**configs)
    configure_logging(
        profile=config.profile,
        log_level=config.log_level,
        name=config.logger_name,
    )

    root_app = web.Application()
    root_app["safir/config"] = config
    setup_metadata(package_name="ook", app=root_app)
    setup_middleware(root_app)
    root_app.add_routes(init_internal_routes())
    root_app.cleanup_ctx.append(init_http_session)
    root_app.cleanup_ctx.append(configure_kafka_ssl)
    root_app.cleanup_ctx.append(init_recordname_schema_manager)
    root_app.cleanup_ctx.append(init_kafka_producer)
    root_app.cleanup_ctx.append(init_kafka_consumer)
    root_app.cleanup_ctx.append(init_algolia_client)

    sub_app = web.Application()
    setup_middleware(sub_app)
    sub_app.add_routes(init_external_routes())
    root_app.add_subapp(f'/{root_app["safir/config"].name}', sub_app)

    return root_app


def setup_middleware(app: web.Application) -> None:
    """Add middleware to the application."""
    app.middlewares.append(bind_logger)


async def init_kafka_consumer(app: web.Application) -> AsyncGenerator:
    """Initialize the Kafka consumer."""
    # Start-up phase
    consumer_task = app.loop.create_task(consume_events(app))
    app["ook/events_consumer_task"] = consumer_task

    yield

    # Tear-down phase
    consumer_task.cancel()
    await consumer_task


async def init_algolia_client(app: web.Application) -> AsyncGenerator:
    """Initialize the Algolia client."""
    app_id = app["safir/config"].algolia_app_id
    api_key = app["safir/config"].algolia_api_key

    if app_id is not None and api_key is not None:
        async with SearchClient.create(
            app_id, api_key.get_secret_value()
        ) as client:
            app["ook/algolia_search"] = client
            yield
    else:
        app["ook/algolia_search"] = None
        yield
