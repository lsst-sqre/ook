"""Models for Kafka messages in topics managed by Ook."""

from __future__ import annotations

from datetime import datetime

from dataclasses_avroschema.avrodantic import AvroBaseModel
from pydantic import Field

from ook.domain.algoliarecord import DocumentSourceType

__all__ = [
    "UrlIngestKeyV1",
    "LtdEditionV1",
    "LtdProjectV1",
    "LtdUrlIngestV1",
]


class UrlIngestKeyV1(AvroBaseModel):
    """Kafka message key model for Slack messages sent by Squarebot."""

    url: str = Field(..., description="The root URL to ingest.")

    class Meta:
        """Metadata for the model."""

        namespace = "lsst.square-events.ook"
        schema_name = "url_ingest_key_v1"


class LtdEditionV1(AvroBaseModel):
    """Information an LTD edition (a sub-model)."""

    url: str = Field(..., description="The API URL of the edition resource.")

    published_url: str = Field(
        ..., description="The published URL of the edition's website."
    )

    slug: str = Field(..., description="The slug of the edition.")

    build_url: str = Field(
        ..., description="The URL of the build associated with the edition."
    )


class LtdProjectV1(AvroBaseModel):
    """Information about an LTD project (a sub-model)."""

    url: str = Field(..., description="The API URL of the project resource.")

    published_url: str = Field(
        ...,
        description=(
            "The URL of the product's website (corresponds to the "
            "published_url of the default edition)."
        ),
    )

    slug: str = Field(..., description="The slug of the project.")


class LtdUrlIngestV1(AvroBaseModel):
    """Kafka message value model for a request to ingest a URL hosted on
    LSST the Docs.

    This value schema should be paired with `UrlIngestKeyV1` for
    the key schema.
    """

    content_type: DocumentSourceType = Field(
        ..., description="The type of content expected at the URL."
    )

    request_timestamp: datetime = Field(
        ..., description="The time of the ingest request."
    )

    update_timestamp: datetime = Field(
        ..., description="The time the LTD updated."
    )

    url: str = Field(..., description="The URL to ingest.")

    edition: LtdEditionV1 = Field(..., description="The LTD edition.")

    project: LtdProjectV1 = Field(..., description="The LTD project.")

    class Meta:
        """Metadata for the model."""

        namespace = "lsst.square-events.ook"
        schema_name = "ltd_url_ingest_v1"
