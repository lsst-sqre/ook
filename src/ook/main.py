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

from ook.dependencies.context import context_dependency

from . import kafkarouter  # delay importing kafka_router for test reconfig
from .config import config
from .handlers.external.paths import external_router
from .handlers.internal.paths import internal_router

__all__ = ["create_app", "create_openapi"]


def create_app() -> FastAPI:
    """Create the main FastAPI application for Ook."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator:
        """Context manager for the application lifespan."""
        logger = get_logger("ook")
        logger.info("Ook is starting up.")

        logger.info(
            "Schema Registry configuration",
            registry_url=config.registry_url,
            subject_suffix=config.subject_suffix,
            subject_compatibility=config.subject_compatibility,
        )
        logger.info(
            "Configured Kafka",
            bootstrap_servers=config.kafka.bootstrap_servers,
        )

        await context_dependency.initialize()

        async with kafkarouter.kafka_router.lifespan_context(app):
            logger.info("Ook start up complete.")
            yield

        # Shut down
        logger.info("Ook is shutting down.")

        await context_dependency.aclose()

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
    """The main FastAPI application for squarebot."""

    # Attach the routers.
    app.include_router(internal_router)
    app.include_router(external_router, prefix=config.path_prefix)
    app.include_router(kafkarouter.kafka_router)

    # Set up middleware
    app.add_middleware(XForwardedMiddleware)

    return app


def create_openapi() -> str:
    """Create the OpenAPI spec for static documentation."""
    app = create_app()
    spec = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    return json.dumps(spec)
