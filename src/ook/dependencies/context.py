"""Request context dependency for FastAPI.

This dependency gathers a variety of information into a single object for the
convenience of writing request handlers.  It also provides a place to store a
`structlog.BoundLogger` that can gather additional context during processing,
including from dependencies.
"""

from dataclasses import dataclass
from typing import Any

from aiokafka import AIOKafkaProducer
from algoliasearch.search_client import SearchClient
from fastapi import Depends, Request
from httpx import AsyncClient
from kafkit.fastapi.dependencies.aiokafkaproducer import (
    kafka_producer_dependency,
)
from kafkit.fastapi.dependencies.pydanticschemamanager import (
    pydantic_schema_manager_dependency,
)
from kafkit.registry.manager import PydanticSchemaManager
from safir.dependencies.http_client import http_client_dependency
from safir.dependencies.logger import logger_dependency
from structlog.stdlib import BoundLogger

from ook.dependencies.algoliasearch import algolia_client_dependency

from ..services.factory import Factory

__all__ = [
    "ContextDependency",
    "RequestContext",
    "context_dependency",
]


@dataclass(slots=True)
class RequestContext:
    """Holds the incoming request and its surrounding context.

    The primary reason for the existence of this class is to allow the
    functions involved in request processing to repeated rebind the request
    logger to include more information, without having to pass both the
    request and the logger separately to every function.
    """

    request: Request
    """The incoming request."""

    logger: BoundLogger
    """The request logger, rebound with discovered context."""

    factory: Factory
    """The component factory."""

    def rebind_logger(self, **values: Any) -> None:
        """Add the given values to the logging context.

        Parameters
        ----------
        **values
            Additional values that should be added to the logging context.
        """
        self.logger = self.logger.bind(**values)
        self.factory.set_logger(self.logger)


class ContextDependency:
    """Provide a per-request context as a FastAPI dependency.

    Each request gets a `RequestContext`.  To save overhead, the portions of
    the context that are shared by all requests are collected into the single
    process-global `~gafaelfawr.factory.ProcessContext` and reused with each
    request.
    """

    def __init__(self) -> None:
        pass

    async def __call__(
        self,
        request: Request,
        logger: BoundLogger = Depends(logger_dependency),
        http_client: AsyncClient = Depends(http_client_dependency),
        kafka_producer: AIOKafkaProducer = Depends(kafka_producer_dependency),
        schema_manager: PydanticSchemaManager = Depends(
            pydantic_schema_manager_dependency
        ),
        algolia_client: SearchClient = Depends(algolia_client_dependency),
    ) -> RequestContext:
        """Create a per-request context and return it."""
        return RequestContext(
            request=request,
            logger=logger,
            factory=Factory(
                logger=logger,
                http_client=http_client,
                kafka_producer=kafka_producer,
                schema_manager=schema_manager,
                algolia_client=algolia_client,
            ),
        )


context_dependency = ContextDependency()
"""The dependency that will return the per-request context."""
