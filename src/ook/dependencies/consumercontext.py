"""A dependency for providing context to consumers."""

from dataclasses import dataclass
from typing import Annotated, Any

from aiokafka import ConsumerRecord
from fastapi import Depends
from faststream import context
from faststream.kafka.fastapi import KafkaMessage
from safir.dependencies.db_session import db_session_dependency
from sqlalchemy.ext.asyncio import async_scoped_session
from structlog import get_logger
from structlog.stdlib import BoundLogger

from ..factory import Factory, ProcessContext


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
    """

    def __init__(self) -> None:
        self._process_context: ProcessContext | None = None

    async def __call__(
        self,
        session: Annotated[
            async_scoped_session, Depends(db_session_dependency)
        ],
    ) -> ConsumerContext:
        """Create a per-request context."""
        # Get the message from the FastStream context
        message: KafkaMessage = context.get_local("message")
        if isinstance(message.raw_message, tuple):
            record = message.raw_message[0]
        else:
            record = message.raw_message

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
