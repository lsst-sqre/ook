"""A dependency for providing context to consumers."""

from dataclasses import dataclass
from typing import Annotated, Any

from aiokafka import ConsumerRecord
from fastapi import Depends
from safir.dependencies.db_session import db_session_dependency
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger
from structlog.stdlib import BoundLogger

from ..factory import Factory, ProcessContext
from ..messagecontext import current_message

__all__ = [
    "ConsumerContext",
    "ConsumerContextDependency",
    "consumer_context_dependency",
]


@dataclass(slots=True, kw_only=True)
class ConsumerContext:
    """Context for consumers."""

    logger: BoundLogger
    """Logger for the consumer."""

    factory: Factory
    """Factory for creating services."""

    record: ConsumerRecord | None = None
    """The Kafka record being processed."""

    def rebind_logger(self, **values: Any) -> None:
        """Add the given values to the logging context.

        Parameters
        ----------
        **values
            Additional values that should be added to the logging context.
        """
        self.logger = self.logger.bind(**values)
        self.factory.set_logger(self.logger)


class ConsumerContextDependency:
    """Provide a per-message context as a dependency for a FastStream consumer.

    Each message handler class gets a `ConsumerContext`.  To save overhead, the
    portions of the context that are shared by all requests are collected into
    the single process-global `~ook.factory.ProcessContext` and reused
    with each request.

    The message itself comes from `MessageContextMiddleware`, which must be
    registered on the broker, rather than from a FastStream ``Context``
    parameter (see the middleware for why).
    """

    def __init__(self) -> None:
        self._process_context: ProcessContext | None = None

    async def __call__(
        self,
        session: Annotated[AsyncSession, Depends(db_session_dependency)],
    ) -> ConsumerContext:
        """Create a per-request context."""
        message = current_message.get()
        if message is None:
            msg = (
                "No message is being consumed on this task; is "
                "MessageContextMiddleware registered on the broker?"
            )
            raise RuntimeError(msg)
        record: ConsumerRecord | tuple[ConsumerRecord, ...] = (
            message.raw_message
        )

        # Get the message from the FastStream context
        if isinstance(record, tuple):
            record = record[0]

        # Add the Kafka context to the logger
        logger = get_logger(__name__)  # eventually use a logger dependency
        kafka_context = {
            "topic": record.topic,
            "offset": record.offset,
            "partition": record.partition,
        }
        logger = logger.bind(kafka=kafka_context)

        return ConsumerContext(
            logger=logger,
            factory=Factory(
                logger=logger,
                session=session,
                process_context=self.process_context,
            ),
        )

    @property
    def process_context(self) -> ProcessContext:
        """The underlying process context, primarily for use in tests."""
        if not self._process_context:
            raise RuntimeError("ConsumerContextDependency not initialized")
        return self._process_context

    async def initialize(self) -> None:
        """Initialize the process-wide shared context."""
        if self._process_context:
            await self._process_context.aclose()
        self._process_context = await ProcessContext.create()

    async def aclose(self) -> None:
        """Clean up the per-process configuration."""
        if self._process_context:
            await self._process_context.aclose()
        self._process_context = None


consumer_context_dependency = ConsumerContextDependency()
"""The dependency that will return the per-request context."""
