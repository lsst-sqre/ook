"""Application factory for Ook.

Notes
-----
Be aware that, following the normal pattern for FastAPI services, the app is
constructed when this module is loaded and is not deferred until a function is
called.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import metadata, version

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from faststream_fastapi import FastStreamAPI
from safir.database import create_database_engine, is_database_current
from safir.dependencies.db_session import db_session_dependency
from safir.fastapi import ClientRequestError, client_request_error_handler
from safir.logging import configure_logging, configure_uvicorn_logging
from safir.middleware.x_forwarded import XForwardedMiddleware
from structlog import get_logger

from .config import config
from .dependencies.consumercontext import consumer_context_dependency
from .dependencies.context import context_dependency
from .handlers.admin import admin_router
from .handlers.authors import authors_router
from .handlers.glossary import glossary_router
from .handlers.ingest import ingest_router
from .handlers.internal import internal_router
from .handlers.intersphinx import intersphinx_router

# Import the kafka broker module and also load the handler functions.
from .handlers.kafka import kafka_broker  # type: ignore [attr-defined]
from .handlers.linkcheck import linkcheck_router
from .handlers.links import links_router
from .handlers.resources import resources_router
from .handlers.root import root_router

__all__ = ["app", "create_openapi"]


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI) -> AsyncIterator:
    """Context manager for the application lifespan."""
    logger = get_logger("ook")
    logger.info("Ook is starting up.")

    logger.info(
        "Configured Kafka",
        bootstrap_servers=config.kafka.bootstrap_servers,
        security_protocol=config.kafka.security_protocol.name,
        ingest_topic=config.ingest_kafka_topic,
        linkcheck_topic=config.linkcheck_kafka_topic,
        consumer_group=config.kafka_consumer_group_id,
    )

    await context_dependency.initialize()
    await consumer_context_dependency.initialize()

    engine = create_database_engine(
        config.database_url, config.database_password
    )
    if not await is_database_current(engine, logger):
        raise RuntimeError("Database schema out of date")
    await engine.dispose()
    await db_session_dependency.initialize(
        config.database_url, config.database_password, pool_pre_ping=True
    )

    logger.info("Ook start up complete.")
    yield

    # Shut down
    logger.info("Ook is shutting down.")

    await db_session_dependency.aclose()
    await context_dependency.aclose()
    await consumer_context_dependency.aclose()

    logger.info("Ook shut down up complete.")


configure_logging(
    profile=config.profile,
    log_level=config.log_level,
    name="ook",
)
configure_uvicorn_logging(config.log_level)

fastapi_app = FastAPI(
    title="Ook",
    description=metadata("ook")["Summary"],
    version=version("ook"),
    openapi_url=f"{config.path_prefix}/openapi.json",
    openapi_tags=[
        {
            "name": "links",
            "description": "Documentation links for different domains.",
        },
        {
            "name": "glossary",
            "description": "Glossary terms.",
        },
        {
            "name": "authors",
            "description": "Author information.",
        },
        {
            "name": "resources",
            "description": "Bibliographic resources.",
        },
        {
            "name": "ingest",
            "description": "Ingest services.",
        },
        {
            "name": "linkcheck",
            "description": (
                "External link checking for documentation projects. "
                "Submissions should be protected via Gafaelfawr ingress "
                "configuration."
            ),
        },
        {
            "name": "intersphinx",
            "description": (
                "Cached Sphinx intersphinx object inventories. These "
                "endpoints should be protected via Gafaelfawr ingress "
                "configuration."
            ),
        },
        {
            "name": "admin",
            "description": (
                "Administrative operations. These endpoints should be "
                "protected via Gafaelfawr ingress configuration."
            ),
        },
        {"name": "default", "description": "Application metadata."},
    ],
    docs_url=f"{config.path_prefix}/docs",
    redoc_url=f"{config.path_prefix}/redoc",
    lifespan=lifespan,
)
"""The inner FastAPI application for ook."""

# Attach the routers. Prefixes are set in the routers themselves.
fastapi_app.include_router(internal_router)
fastapi_app.include_router(root_router)
fastapi_app.include_router(authors_router)
fastapi_app.include_router(glossary_router)
fastapi_app.include_router(ingest_router)
fastapi_app.include_router(linkcheck_router)
fastapi_app.include_router(intersphinx_router)
fastapi_app.include_router(links_router)
fastapi_app.include_router(resources_router)
fastapi_app.include_router(admin_router)

# Set up middleware
fastapi_app.add_middleware(XForwardedMiddleware)

# Set up error handling
fastapi_app.exception_handler(ClientRequestError)(client_request_error_handler)

# Wrap the FastAPI app with the FastStream broker. This must come after
# every subscriber-module import (guaranteed by the `from .handlers.kafka
# import kafka_broker` import above) so that FastStreamAPI wraps all of
# ook's Kafka subscribers.
app = FastStreamAPI(
    kafka_broker,
    application=fastapi_app,
    asyncapi_path=f"{config.path_prefix}/asyncapi",
)


def create_openapi() -> str:
    """Create the OpenAPI spec for static documentation."""
    spec = get_openapi(
        title=fastapi_app.title,
        version=fastapi_app.version,
        description=fastapi_app.description,
        tags=fastapi_app.openapi_tags,
        routes=fastapi_app.routes,
    )
    return json.dumps(spec)
