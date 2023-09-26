"""Tests for the Kafka models in ``ook.domain.kafka``."""

from __future__ import annotations

import json

from ook.domain.kafka import LtdUrlIngestV2, UrlIngestKeyV1


def test_url_ingest_key_v1() -> None:
    """Test ``UrlIngestKeyV1``."""
    schema = json.loads(UrlIngestKeyV1.avro_schema())
    assert schema["name"] == "url_ingest_key_v1"
    assert schema["namespace"] == "lsst.square-events.ook"


def test_ltd_url_ingest_v2() -> None:
    """Test the ``LtdUrlIngestV2`` model."""
    schema = json.loads(LtdUrlIngestV2.avro_schema())
    assert schema["name"] == "ltd_url_ingest_v2"
    assert schema["namespace"] == "lsst.square-events.ook"
