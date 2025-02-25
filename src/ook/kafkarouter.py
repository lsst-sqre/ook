"""The FastStream Kafka router."""

# This module holds kafka_router in a separate module to avoid circular
# imports with the consumer_context_dependency.

from __future__ import annotations

from faststream.kafka.fastapi import KafkaRouter

from .config import config

__all__ = ["kafka_router"]


kafka_router = KafkaRouter(**config.kafka.to_faststream_params())
