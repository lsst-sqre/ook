"""Routes Kafka messages to processing handlers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, Self, cast

from aiokafka import AIOKafkaConsumer, ConsumerRecord
from dataclasses_avroschema.avrodantic import AvroBaseModel
from kafkit.registry import UnmanagedSchemaError
from kafkit.registry.manager import PydanticSchemaManager
from structlog import get_logger

from ook.config import config
from ook.domain.kafka import LtdUrlIngestV1, UrlIngestKeyV1
from ook.handlers.kafka.handlers import handle_ltd_document_ingest
from ook.services.factory import Factory


class HandlerProtocol(Protocol):
    """A protocol for a Kafka message handler."""

    async def __call__(
        self,
        *,
        message_metadata: MessageMetadata,
        key: AvroBaseModel,
        value: AvroBaseModel,
        **kwargs: Any,
    ) -> None:
        """Handle a Kafka message."""


@dataclass
class Route:
    """A pointer to a route for a Kafka message.

    A route is associated with a specific set of topics and Pydantic models
    for the key and value.
    """

    callback: HandlerProtocol
    """The callback to invoke when a message is routed to this route.

    The callback is a async function that takes three keyword arguments, plus
    any additional keyword arguments specified in `kwargs`:

    1. The Kafka message metadata (`MessageMetadata`).
    2. The deserialized key (`AvroBaseModel`).
    3. The deserialized value (`AvroBaseModel`).
    """

    topics: Sequence[str]
    """The Kafka topics that this route is associated with."""

    key_models: Sequence[type[AvroBaseModel]]
    """The Pydantic model types that this route is associated with for the
    message key.
    """

    value_models: Sequence[type[AvroBaseModel]]
    """The Pydantic model types that this route is associated with for the
    message value.
    """

    kwargs: dict[str, Any] | None = None
    """Keyword arguments to pass to the callback."""

    def matches(
        self, topic: str, key: AvroBaseModel, value: AvroBaseModel
    ) -> bool:
        """Determine if this route matches the given topic name and Pydantic
        key and value models.
        """
        return (
            topic in self.topics
            and type(key) in self.key_models
            and type(value) in self.value_models
        )


@dataclass
class MessageMetadata:
    """Metadata about a Kafka message."""

    topic: str
    """The Kafka topic name."""

    offset: int
    """The Kafka message offset in the partition"""

    partition: int
    """The Kafka partition."""

    timestamp: datetime
    """The Kafka message timestamp."""

    serialized_key_size: int
    """The size of the serialized key, in bytes."""

    serialized_value_size: int
    """The size of the serialized value, in bytes."""

    headers: dict[str, bytes]
    """The Kafka message headers."""

    @classmethod
    def from_consumer_record(cls, record: ConsumerRecord) -> Self:
        """Create a MessageMetadata instance from a ConsumerRecord."""
        return cls(
            topic=record.topic,
            offset=record.offset,
            partition=record.partition,
            timestamp=datetime.fromtimestamp(record.timestamp, tz=UTC),
            serialized_key_size=record.serialized_key_size,
            serialized_value_size=record.serialized_value_size,
            headers=record.headers,
        )


class PydanticAIOKafkaConsumer:
    """A Kafka consumer that deserializes messages into Pydantic models and
    routes them to handlers.
    """

    def __init__(
        self,
        *,
        schema_manager: PydanticSchemaManager,
        consumer: AIOKafkaConsumer,
    ) -> None:
        self._schema_manager = schema_manager
        self._consumer = consumer
        self._routes: list[Route] = []

    async def start(self) -> None:
        """Start the consumer."""
        await self._consumer.start()
        try:
            # Consume messages
            async for msg in self._consumer:
                # print("consumed: ", msg.topic, msg.partition, msg.offset,
                #     msg.key, msg.value, msg.timestamp)
                await self._handle_message(msg)
        finally:
            # Will leave consumer group; perform autocommit if enabled.
            await self._consumer.stop()

    async def _handle_message(self, msg: ConsumerRecord) -> None:
        """Handle a Kafka message by deserializing the key and value into
        Pydantic models and routing to a handler.
        """
        try:
            key = await self._schema_manager.deserialize(msg.key)
            value = await self._schema_manager.deserialize(msg.value)
        except UnmanagedSchemaError:
            # TODO log
            return
        message_metadata = MessageMetadata.from_consumer_record(msg)
        for route in self._routes:
            if route.matches(msg.topic, key, value):
                kwargs = route.kwargs or {}
                await route.callback(
                    message_metadata=message_metadata,
                    key=key,
                    value=value,
                    **kwargs,
                )
                break

    async def register_models(
        self, models: Iterable[type[AvroBaseModel]]
    ) -> None:
        """Pre-register Pydantic models with the schema manager."""
        await self._schema_manager.register_models(models)

    async def add_route(
        self,
        callback: HandlerProtocol,
        topics: Sequence[str],
        key_models: Sequence[type[AvroBaseModel]],
        value_models: Sequence[type[AvroBaseModel]],
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Register a handler for a Kafka topic given the support key and
        value models.
        """
        await self.register_models(key_models)
        await self.register_models(value_models)
        self._routes.append(
            Route(callback, topics, key_models, value_models, kwargs)
        )


async def consume_kafka_messages() -> None:
    """Consume Kafka messages."""
    logger = get_logger("ook")
    factory = await Factory.create(logger=logger)
    schema_manager = factory.schema_manager
    aiokafka_consumer = AIOKafkaConsumer(
        config.ingest_kafka_topic,
        bootstrap_servers=config.kafka.bootstrap_servers,
        group_id=config.kafka_consumer_group_id,
        security_protocol=config.kafka.security_protocol,
        ssl_context=config.kafka.ssl_context,
    )

    consumer = PydanticAIOKafkaConsumer(
        schema_manager=schema_manager, consumer=aiokafka_consumer
    )
    await consumer.add_route(
        cast(HandlerProtocol, handle_ltd_document_ingest),
        [config.ingest_kafka_topic],
        [UrlIngestKeyV1],
        [LtdUrlIngestV1],
    )
    await consumer.start()
