"""Expose the Kafka message being consumed to dependencies."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from faststream.message import StreamMessage
from faststream.middlewares import BaseMiddleware

current_message: ContextVar[StreamMessage[Any] | None] = ContextVar(
    "ook_current_message", default=None
)
"""The FastStream message being consumed on the current task, if any.

Set by `MessageContextMiddleware` for the duration of each message and read
by `ConsumerContextDependency`.
"""


class MessageContextMiddleware(BaseMiddleware[Any, Any]):
    """Expose the message being consumed to FastAPI-style dependencies.

    FastStream stores the current message in its own context repository,
    which ``faststream_fastapi.Context("message")`` used to read. Since
    faststream 0.7.5, an application-level ``FastDependsConfig`` merged into
    a broker wraps the broker's context in a ``ContextRepoComposition`` and
    scopes the per-message values inside that composition, while
    faststream-fastapi (1.3.1) still hands its ``Context()`` dependencies the
    application-level ``ContextRepo``, which no longer sees ``message`` and
    resolves it to ``EMPTY``. This middleware sidesteps that plumbing: the
    subscriber hands middlewares the parsed message directly, so it is
    published on a `contextvars.ContextVar` that the handler's dependencies,
    running on the same task, can read.
    """

    async def consume_scope(
        self,
        call_next: Callable[[Any], Awaitable[Any]],
        msg: StreamMessage[Any],
    ) -> Any:
        """Publish the message to the current task while it is handled."""
        token = current_message.set(msg)
        try:
            return await call_next(msg)
        finally:
            current_message.reset(token)
