"""Tests for the message context middleware."""

from __future__ import annotations

from typing import Any

import pytest
from aiokafka import ConsumerRecord
from faststream.kafka.message import FakeConsumer, KafkaMessage

from ook.kafkabroker import kafka_broker
from ook.messagecontext import MessageContextMiddleware, current_message


def make_record(*, topic: str, partition: int, offset: int) -> ConsumerRecord:
    return ConsumerRecord(
        topic=topic,
        partition=partition,
        offset=offset,
        timestamp=0,
        timestamp_type=0,
        key=None,
        value=b"{}",
        checksum=None,
        serialized_key_size=-1,
        serialized_value_size=2,
        headers=[],
    )


@pytest.mark.asyncio
async def test_middleware_exposes_the_message_while_it_is_handled() -> None:
    """The ContextVar sees the message the middleware is wrapping.

    This is the seam that replaced ``faststream_fastapi.Context("message")``,
    which stopped resolving under faststream 0.7.5.
    """
    record = make_record(topic="topic", partition=3, offset=42)
    message = KafkaMessage(record, b"{}", consumer=FakeConsumer())
    middleware = MessageContextMiddleware(record, context=kafka_broker.context)

    async def handler(msg: Any) -> None:
        assert current_message.get() is message

    await middleware.consume_scope(handler, message)


@pytest.mark.asyncio
async def test_batch_message_is_exposed_too() -> None:
    first = make_record(topic="topic", partition=0, offset=7)
    second = make_record(topic="topic", partition=0, offset=8)
    message = KafkaMessage((first, second), b"[]", consumer=FakeConsumer())
    middleware = MessageContextMiddleware(first, context=kafka_broker.context)

    async def handler(msg: Any) -> None:
        assert current_message.get() is message

    await middleware.consume_scope(handler, message)


@pytest.mark.asyncio
async def test_message_is_cleared_after_handling() -> None:
    record = make_record(topic="topic", partition=0, offset=1)
    message = KafkaMessage(record, b"{}", consumer=FakeConsumer())
    middleware = MessageContextMiddleware(record, context=kafka_broker.context)

    async def handler(msg: Any) -> None:
        return None

    await middleware.consume_scope(handler, message)

    assert current_message.get() is None


@pytest.mark.asyncio
async def test_message_is_cleared_when_the_handler_fails() -> None:
    record = make_record(topic="topic", partition=0, offset=1)
    message = KafkaMessage(record, b"{}", consumer=FakeConsumer())
    middleware = MessageContextMiddleware(record, context=kafka_broker.context)

    async def handler(msg: Any) -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await middleware.consume_scope(handler, message)

    assert current_message.get() is None
