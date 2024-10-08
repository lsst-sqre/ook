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
from safir.logging import configure_logging, configure_uvicorn_logging
from safir.middleware.x_forwarded import XForwardedMiddleware
from structlog import get_logger

from ook.dependencies.consumercontext import consumer_context_dependency
from ook.dependencies.context import context_dependency

from .config import config
from .handlers.external.paths import external_router
from .handlers.internal.paths import internal_router

# Import kafka router and also load the handler functions.
from .handlers.kafka import kafka_router  # type: ignore [attr-defined]

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

    async with kafka_router.lifespan_context(app):
        logger.info("Ook start up complete.")
        yield

    # Shut down
    logger.info("Ook is shutting down.")

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
    docs_url=f"{config.path_prefix}/docs",
    redoc_url=f"{config.path_prefix}/redoc",
    lifespan=lifespan,
)
"""The main FastAPI application for ook."""

# Attach the routers.
app.include_router(internal_router)
app.include_router(external_router, prefix=config.path_prefix)
app.include_router(kafka_router)

# Set up middleware
app.add_middleware(XForwardedMiddleware)


def create_openapi() -> str:
    """Create the OpenAPI spec for static documentation."""
    spec = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    return json.dumps(spec)
