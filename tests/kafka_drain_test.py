"""Tests for the per-test Kafka drain barrier in ``tests.support.kafka``.

These exercise the tracker through the broker middleware it installs, so
they need no Kafka: the middleware is driven directly with stand-in
consumer records and publish commands.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from faststream._internal.context.repository import ContextRepo

from tests.support.kafka import KafkaDrainTimeoutError, KafkaWorkTracker

TOPIC = "ook.linkcheck"
HANDLER = "handle_linkcheck_execution"


def make_tracker() -> KafkaWorkTracker:
    """Return a tracker armed with one subscribed topic."""
    tracker = KafkaWorkTracker()
    tracker.arm({TOPIC: [HANDLER]})
    return tracker


def consumer_record(
    topic: str = TOPIC, *, partition: int = 0, offset: int = 7
) -> Any:
    """Return a stand-in for an aiokafka ``ConsumerRecord``."""
    return SimpleNamespace(topic=topic, partition=partition, offset=offset)


def publish_command(topic: str = TOPIC, *bodies: bytes) -> Any:
    """Return a stand-in for a FastStream ``PublishCommand``."""
    return SimpleNamespace(destination=topic, batch_bodies=bodies or (b"{}",))


@pytest.mark.asyncio
async def test_drain_returns_without_waiting_when_no_work_is_outstanding() -> (
    None
):
    """A test that left no Kafka work behind pays nothing at the barrier."""
    tracker = make_tracker()
    assert tracker.is_idle

    # A zero timeout would raise if the barrier awaited anything at all.
    await tracker.drain(timeout=0.0)


@pytest.mark.asyncio
async def test_drain_waits_for_a_published_message_to_be_handled() -> None:
    """The barrier stays closed from publish until the handler returns."""
    tracker = make_tracker()
    context = ContextRepo()

    publisher = tracker(None, context=context)
    await publisher.publish_scope(_succeed, publish_command())
    idle_after_publish = tracker.is_idle

    async with tracker(consumer_record(), context=context):
        idle_while_handling = tracker.is_idle

    assert not idle_after_publish, "a published message is outstanding"
    assert not idle_while_handling, "the handler was still executing"
    assert tracker.is_idle
    await tracker.drain(timeout=0.0)


@pytest.mark.asyncio
async def test_drain_waits_for_every_message_of_a_batch_publish() -> None:
    """A batch publish counts each message it carries."""
    tracker = make_tracker()
    context = ContextRepo()

    publisher = tracker(None, context=context)
    await publisher.publish_scope(
        _succeed, publish_command(TOPIC, b"1", b"2", b"3")
    )

    for _ in range(2):
        async with tracker(consumer_record(), context=context):
            pass
    idle_after_two_of_three = tracker.is_idle

    async with tracker(consumer_record(), context=context):
        pass

    assert not idle_after_two_of_three, "a batch message was still unhandled"
    assert tracker.is_idle


@pytest.mark.asyncio
async def test_a_failed_publish_leaves_nothing_to_drain() -> None:
    """A publish that raises never enqueued anything to wait for."""
    tracker = make_tracker()
    publisher = tracker(None, context=ContextRepo())

    with pytest.raises(RuntimeError, match="kafka is down"):
        await publisher.publish_scope(_fail, publish_command())

    assert tracker.is_idle


@pytest.mark.asyncio
async def test_messages_on_unsubscribed_topics_are_not_drained() -> None:
    """Nothing in this process consumes a topic without a subscriber, so
    waiting for one would hang until the timeout.
    """
    tracker = make_tracker()
    publisher = tracker(None, context=ContextRepo())

    await publisher.publish_scope(_succeed, publish_command("ook.unwatched"))

    assert tracker.is_idle


@pytest.mark.asyncio
async def test_an_unarmed_tracker_drains_immediately() -> None:
    """Without a running subscriber (a worker that never starts the app)
    there is nothing to drain, so the barrier must not wait.
    """
    tracker = KafkaWorkTracker()
    publisher = tracker(None, context=ContextRepo())

    await publisher.publish_scope(_succeed, publish_command())

    assert tracker.is_idle
    await tracker.drain(timeout=0.0)


@pytest.mark.asyncio
async def test_timeout_names_the_topic_and_handler_of_unconsumed_work() -> (
    None
):
    """A drain timeout must be diagnosable where it happens rather than
    surfacing as an unrelated failure in the next test.
    """
    tracker = make_tracker()
    publisher = tracker(None, context=ContextRepo())
    await publisher.publish_scope(_succeed, publish_command())

    with pytest.raises(KafkaDrainTimeoutError) as excinfo:
        await tracker.drain(timeout=0.05)

    message = str(excinfo.value)
    assert TOPIC in message
    assert HANDLER in message


@pytest.mark.asyncio
async def test_timeout_names_a_handler_that_is_still_executing() -> None:
    """The error names the running handler, not just the topic."""
    tracker = make_tracker()
    context = ContextRepo()

    async with tracker(consumer_record(offset=42), context=context):
        with pytest.raises(KafkaDrainTimeoutError) as excinfo:
            await tracker.drain(timeout=0.05)

    message = str(excinfo.value)
    assert HANDLER in message
    assert TOPIC in message
    assert "42" in message


async def _succeed(cmd: Any) -> str:
    return "published"


async def _fail(cmd: Any) -> str:
    raise RuntimeError("kafka is down")
