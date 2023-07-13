"""Routes Kafka messages to processing handlers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from aiokafka import AIOKafkaConsumer, ConsumerRecord
from dataclasses_avroschema.avrodantic import AvroBaseModel
from httpx import AsyncClient
from kafkit.registry import UnmanagedSchemaError
from kafkit.registry.httpx import RegistryApi
from kafkit.registry.manager import PydanticSchemaManager

from ook.config import config


@dataclass
class Route:
    """A pointer to a route for a Kafka message.

    A route is associated with a specific set of topics and Pydantic models
    for the key and value.
    """

    callback: Callable[[str, AvroBaseModel, AvroBaseModel], None]
    """The callback to invoke when a message is routed to this route.

    The callback is a async function that takes three arguments:

    1. The Kafka topic name (`str`).
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
            topic in self._topics
            and type(key) in self._key_models
            and type(value) in self._value_models
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
        for route in self._routes:
            if route.matches(msg.topic, key, value):
                kwargs = route.kwargs or {}
                await route.callback(msg.topic, key, value, **kwargs)
                break

    async def register_models(
        self, models: Iterable[type[AvroBaseModel]]
    ) -> None:
        """Pre-register Pydantic models with the schema manager."""
        await self._schema_manager.register_models(models)

    async def add_route(
        self,
        callback: Callable[[str, AvroBaseModel, AvroBaseModel], None],
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


async def consume_kafka_messages(http_client: AsyncClient) -> None:
    """Consume Kafka messages."""
    # Set up the schema manager
    registry = RegistryApi(http_client=http_client, url=config.registry_url)
    schema_manager = PydanticSchemaManager(registry=registry)
    aiokafka_consumer = AIOKafkaConsumer(
        [config.ingest_kafka_topic],  # TODO add topics
        bootstrap_servers=config.kafka.bootstrap_servers,
        group_id=config.kafka_consumer_group_id,
        security_protocol=config.kafka.security_protocol,
        ssl_context=config.kafka.ssl_context,
    )

    consumer = PydanticAIOKafkaConsumer(
        schema_manager=schema_manager, consumer=aiokafka_consumer
    )
    # TODO add routes
    await consumer.start()
