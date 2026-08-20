"""A drain barrier for the Kafka work a test leaves behind.

The whole test session shares one FastStream broker (see ``_app_lifespan``
in `tests.conftest`) and its subscribers keep running between tests. A test
that publishes a message and then returns -- ``POST
/ook/linkcheck/checks`` answers ``202`` as soon as the message is enqueued
-- therefore leaves a Kafka handler executing into the *next* test, where
its HTTP fetches hit that test's respx router (raising
``AllMockedAssertionError``, or escaping to the real network) and its
committed rows show up in that test's freshly truncated database. The
``lock_timeout`` retry in `tests.support.database` only absorbs a handler
that already holds locks when the truncate runs; one that starts *after*
the truncate commits into the next test unimpeded.

`KafkaWorkTracker` closes that hole. Registered as a broker middleware, it
counts every message published to a subscribed topic and every handler
execution the broker starts and finishes, and `KafkaWorkTracker.drain`
waits until neither is outstanding. The ``app`` and ``factory`` fixtures
call it right after the test's body, which is before the respx routers
those fixtures depend on are torn down and before the next test truncates
the database.

The barrier costs nothing when a test leaves no Kafka work behind: the
tracker is already idle, so `KafkaWorkTracker.drain` returns without
awaiting anything.

The tracker only waits for topics whose subscribers were running when it
was armed. A pytest-xdist worker that never runs a test needing the ``app``
fixture never starts the application lifespan, so nothing in that process
would ever consume what its ``factory`` tests publish; an unarmed tracker
reports itself idle instead of hanging until the timeout.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Any, Protocol, cast

from faststream import BaseMiddleware

if TYPE_CHECKING:
    from faststream._internal.context.repository import ContextRepo
    from faststream.kafka import KafkaBroker

__all__ = [
    "DRAIN_TIMEOUT",
    "KafkaDrainTimeoutError",
    "KafkaWorkTracker",
    "kafka_work_tracker",
    "running_subscriptions",
]

DRAIN_TIMEOUT = 30.0
"""Seconds the barrier waits for a test's Kafka work before giving up."""


class KafkaDrainTimeoutError(RuntimeError):
    """Kafka work from a test was still outstanding when the drain barrier
    gave up.

    The message names the handlers that were still running and the topics
    whose messages had not been consumed, so the failure is diagnosable on
    the test that produced the work rather than on whichever test ran next.
    """


@dataclass(frozen=True, slots=True)
class _Execution:
    """A handler execution the broker has started but not yet finished."""

    handler: str
    topic: str
    partition: int | None
    offset: int | None
    started_at: float

    def describe(self, now: float) -> str:
        """Return a one-line description of this execution."""
        location = self.topic
        if self.partition is not None:
            location += f" partition {self.partition}"
        if self.offset is not None:
            location += f" offset {self.offset}"
        return (
            f"{self.handler} on {location},"
            f" running for {now - self.started_at:.1f}s"
        )


class KafkaWorkTracker:
    """Tracks the Kafka work in flight on a shared FastStream broker.

    An instance is both the state and the broker middleware factory:
    FastStream calls it once per published command and once per consumed
    message to build a middleware bound to this tracker.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[str, tuple[str, ...]] = {}
        self._armed = False
        self._unconsumed: Counter[str] = Counter()
        self._executions: dict[int, _Execution] = {}
        self._next_token = 0
        self._idle = asyncio.Event()
        self._idle.set()

    def __call__(
        self, msg: Any | None, /, *, context: ContextRepo
    ) -> BaseMiddleware[Any, Any]:
        """Build the per-message middleware (the ``BrokerMiddleware``
        protocol FastStream expects).
        """
        return _TrackingMiddleware(msg, context=context, tracker=self)

    def install(self, broker: KafkaBroker) -> None:
        """Register this tracker as a middleware on a broker."""
        broker.add_middleware(self)

    def arm(self, subscriptions: Mapping[str, Sequence[str]]) -> None:
        """Start tracking messages on the given topics.

        Parameters
        ----------
        subscriptions
            Maps each topic with a running subscriber to the names of the
            handlers that consume it. Messages published to any other topic
            are ignored, because nothing in this process will consume them.
        """
        self._subscriptions = {
            topic: tuple(handlers)
            for topic, handlers in subscriptions.items()
            if handlers
        }
        self._armed = True
        self._unconsumed.clear()
        self._executions.clear()
        self._idle.set()

    @property
    def is_idle(self) -> bool:
        """Whether all tracked Kafka work has finished."""
        return not self._executions and not any(self._unconsumed.values())

    async def drain(self, *, timeout: float = DRAIN_TIMEOUT) -> None:
        """Wait until every tracked message has been consumed and every
        handler execution has returned.

        Parameters
        ----------
        timeout
            Seconds to wait before giving up.

        Raises
        ------
        KafkaDrainTimeoutError
            Raised if work was still outstanding after ``timeout`` seconds.
        """
        if self.is_idle:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not self.is_idle:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise KafkaDrainTimeoutError(self._describe(timeout))
            try:
                await asyncio.wait_for(self._idle.wait(), remaining)
            except TimeoutError:
                raise KafkaDrainTimeoutError(self._describe(timeout)) from None

    def record_published(self, topic: str, count: int) -> str | None:
        """Record ``count`` messages published to ``topic``.

        Returns
        -------
        str or None
            The topic if it is being tracked, so a failed publish can undo
            the record; `None` otherwise.
        """
        if not self._armed or topic not in self._subscriptions:
            return None
        self._unconsumed[topic] += count
        self._idle.clear()
        return topic

    def discard_published(self, topic: str | None, count: int) -> None:
        """Undo `record_published` for a publish that raised."""
        if topic is None:
            return
        self._unconsumed[topic] = max(0, self._unconsumed[topic] - count)
        self._settle()

    def record_execution_started(self, records: Sequence[Any]) -> int | None:
        """Record the start of a handler execution over ``records``.

        Returns
        -------
        int or None
            A token to hand to `record_execution_finished`, or `None` if
            the tracker is not armed.
        """
        if not self._armed or not records:
            return None
        for record in records:
            topic = _topic_of(record)
            if topic is not None:
                self._unconsumed[topic] = max(0, self._unconsumed[topic] - 1)
        first = records[0]
        topic = _topic_of(first) or "(unknown topic)"
        token = self._next_token
        self._next_token += 1
        self._executions[token] = _Execution(
            handler=self._handlers_of(topic),
            topic=topic,
            partition=getattr(first, "partition", None),
            offset=getattr(first, "offset", None),
            started_at=time.monotonic(),
        )
        self._idle.clear()
        return token

    def record_execution_finished(self, token: int | None) -> None:
        """Record that the execution identified by ``token`` returned."""
        if token is None:
            return
        self._executions.pop(token, None)
        self._settle()

    def _settle(self) -> None:
        """Open the barrier if nothing is outstanding any more."""
        if self.is_idle:
            self._idle.set()

    def _handlers_of(self, topic: str) -> str:
        """Return a display name for the handlers subscribed to a topic."""
        return ", ".join(self._subscriptions.get(topic, ())) or (
            "(no known handler)"
        )

    def _describe(self, timeout: float) -> str:
        """Return the diagnostic message for a drain timeout."""
        now = time.monotonic()
        lines = [
            (
                "Kafka work published by this test was still in flight"
                f" {timeout:.1f}s after the test finished."
            )
        ]
        if self._executions:
            lines.append("Handler executions still running:")
            lines.extend(
                f"  - {execution.describe(now)}"
                for execution in sorted(
                    self._executions.values(), key=lambda e: e.started_at
                )
            )
        outstanding = {
            topic: count
            for topic, count in sorted(self._unconsumed.items())
            if count > 0
        }
        if outstanding:
            lines.append("Published messages not yet consumed:")
            lines.extend(
                f"  - {count} on {topic},"
                f" handled by {self._handlers_of(topic)}"
                for topic, count in outstanding.items()
            )
        return "\n".join(lines)


kafka_work_tracker = KafkaWorkTracker()
"""The tracker for the process-wide FastStream broker.

A singleton because `ook.kafkabroker.kafka_broker` is one too, and because
the ``app`` and ``factory`` fixtures need to reach the same tracker the
session-scoped lifespan armed.
"""


class _KafkaSubscriber(Protocol):
    """The part of a FastStream Kafka subscriber this module reads.

    ``KafkaBroker.subscribers`` is typed as the broker-agnostic base class,
    which knows nothing about topics.
    """

    running: bool

    @property
    def topics(self) -> list[str]: ...

    @property
    def specification(self) -> Any: ...


def running_subscriptions(broker: KafkaBroker) -> dict[str, list[str]]:
    """Map each topic with a running subscriber to its handler names.

    Parameters
    ----------
    broker
        Broker whose subscribers to inspect.

    Returns
    -------
    dict
        Topic name to the names of the handler functions consuming it. A
        subscriber that is not running contributes nothing: its messages
        would never be consumed, so they must not be waited for.
    """
    subscriptions: dict[str, list[str]] = {}
    for base_subscriber in broker.subscribers:
        subscriber = cast("_KafkaSubscriber", base_subscriber)
        if not subscriber.running:
            continue
        name: str = subscriber.specification.call_name
        for topic in subscriber.topics:
            subscriptions.setdefault(topic, []).append(name)
    return subscriptions


class _TrackingMiddleware(BaseMiddleware[Any, Any]):
    """Reports publishes and handler executions to a `KafkaWorkTracker`.

    FastStream builds one of these per published command (with ``msg``
    `None`) and one per consumed message, so all shared state lives on the
    tracker.
    """

    def __init__(
        self,
        msg: Any | None,
        /,
        *,
        context: ContextRepo,
        tracker: KafkaWorkTracker,
    ) -> None:
        super().__init__(msg, context=context)
        self._tracker = tracker
        self._token: int | None = None

    async def on_receive(self) -> None:
        self._token = self._tracker.record_execution_started(
            _consumer_records(self.msg)
        )

    async def after_processed(
        self,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
    ) -> bool | None:
        self._tracker.record_execution_finished(self._token)
        self._token = None
        return False

    async def publish_scope(
        self,
        call_next: Callable[[Any], Awaitable[Any]],
        cmd: Any,
    ) -> Any:
        # Record before publishing: a message can be consumed the instant
        # the send completes, and a record added afterwards could then land
        # after the consumer already accounted for it.
        count = len(getattr(cmd, "batch_bodies", ())) or 1
        topic = self._tracker.record_published(cmd.destination, count)
        try:
            return await call_next(cmd)
        except BaseException:
            self._tracker.discard_published(topic, count)
            raise


def _consumer_records(msg: Any) -> list[Any]:
    """Return the consumer records a subscriber was handed.

    A batch subscriber is handed a sequence of records; a single-message
    subscriber is handed one record.
    """
    if msg is None:
        return []
    if isinstance(msg, list | tuple):
        return list(msg)
    return [msg]


def _topic_of(record: Any) -> str | None:
    """Return the topic of a consumer record, if it has one."""
    topic = getattr(record, "topic", None)
    return topic if isinstance(topic, str) else None
