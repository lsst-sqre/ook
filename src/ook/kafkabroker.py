"""The FastStream Kafka broker."""

# This module holds kafka_broker in a separate module to avoid circular
# imports with the consumer_context_dependency.

from __future__ import annotations

from faststream.kafka import KafkaBroker

from .config import config

__all__ = ["kafka_broker"]


kafka_broker = KafkaBroker(**config.kafka.to_faststream_params())
