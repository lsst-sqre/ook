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
from safir.database import create_database_engine, is_database_current
from safir.dependencies.db_session import db_session_dependency
from safir.fastapi import ClientRequestError, client_request_error_handler
from safir.logging import configure_logging, configure_uvicorn_logging
from safir.middleware.x_forwarded import XForwardedMiddleware
from structlog import get_logger

from .config import config
from .dependencies.consumercontext import consumer_context_dependency
from .dependencies.context import context_dependency
from .handlers.ingest import ingest_router
from .handlers.internal import internal_router

# Import kafka router and also load the handler functions.
from .handlers.kafka import kafka_router  # type: ignore [attr-defined]
from .handlers.links import links_router
from .handlers.root import root_router

__all__ = ["app", "create_openapi"]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator:
    """Context manager for the application lifespan."""
    logger = get_logger("ook")
    logger.info("Ook is starting up.")

    logger.info(
        "Configured Kafka",
        bootstrap_servers=config.kafka.bootstrap_servers,
        security_protocol=config.kafka.security_protocol.name,
        ingest_topic=config.ingest_kafka_topic,
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
        config.database_url, config.database_password
    )

    async with kafka_router.lifespan_context(app):
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

app = FastAPI(
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
            "name": "ingest",
            "description": "Ingest endpoints for Ook.",
        },
    ],
    docs_url=f"{config.path_prefix}/docs",
    redoc_url=f"{config.path_prefix}/redoc",
    lifespan=lifespan,
)
"""The main FastAPI application for ook."""

# Attach the routers. Prefixes are set in the routers themselves.
app.include_router(internal_router)
app.include_router(root_router)
app.include_router(ingest_router)
app.include_router(links_router)
app.include_router(kafka_router)

# Set up middleware
app.add_middleware(XForwardedMiddleware)

# Set up error handling
app.exception_handler(ClientRequestError)(client_request_error_handler)


def create_openapi() -> str:
    """Create the OpenAPI spec for static documentation."""
    spec = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        tags=app.openapi_tags,
        routes=app.routes,
    )
    return json.dumps(spec)
