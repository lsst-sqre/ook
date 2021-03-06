"""Configuration definition."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from pydantic import AnyHttpUrl, BaseSettings, Field, SecretStr, validator

__all__ = ["Configuration"]


if TYPE_CHECKING:
    from typing import Any, Mapping, TypeVar

    ValuesType = Mapping[str, Any]
    ValueType = TypeVar("ValueType")


class ProfileEnum(str, Enum):
    """Application run profile."""

    production = "production"
    development = "development"


class LogLevelEnum(str, Enum):
    """Logging level."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class KafkaProtocolEnum(str, Enum):
    """Kafka protocol."""

    SSL = "SSL"
    PLAINTEXT = "PLAINTEXT"


class SchemaCompatibilityEnum(str, Enum):
    """Schema compatibility settings for the Confluent Schema Registry."""

    BACKWARD = "BACKWARD"
    BACKWARD_TRANSITIVE = "BACKWARD_TRANSITIVE"
    FORWARD = "FORWARD"
    FORWARD_TRANSITIVE = "FORWARD_TRANSITIVE"
    FULL = "FULL"
    FULL_TRANSITIVE = "FULL_TRANSITIVE"
    NONE = "NONE"


class Configuration(BaseSettings):
    """Configuration for ook."""

    name: str = Field(
        "ook",
        env="SAFIR_NAME",
        description=(
            "The application's name, which doubles as the root HTTP "
            "endpoint path."
        ),
    )

    profile: ProfileEnum = Field(
        ProfileEnum.development,
        env="SAFIR_PROFILE",
        description="Application run profile: 'development' or 'production'.",
    )

    logger_name: str = Field(
        "ook",
        env="SAFIR_LOGGER",
        description="The root name of the application's logger.",
    )

    log_level: LogLevelEnum = Field(
        LogLevelEnum.INFO,
        env="SAFIR_LOG_LEVEL",
        description="The log level of the application's logger.",
    )

    kafka_protocol: KafkaProtocolEnum = Field(
        KafkaProtocolEnum.PLAINTEXT,
        env="SAFIR_KAFKA_PROTOCOL",
        description=(
            "The protocol used for communicating with Kafka brokers. The "
            "``SSL`` protocol requires that certificate paths are also "
            "configured."
        ),
    )

    kafka_cluster_ca_path: Optional[Path] = Field(
        None,
        env="SAFIR_KAFKA_CLUSTER_CA",
        description=(
            "The path of the Strimzi-generated SSL cluster CA file for the "
            "Kafka brokers."
        ),
    )

    kafka_client_cert_path: Optional[Path] = Field(
        None,
        env="SAFIR_KAFKA_CLIENT_CERT",
        description=(
            "The path of the Strimzi-generated SSL cluster cert file for the "
            "Kafka client."
        ),
    )

    kafka_client_key_path: Optional[Path] = Field(
        None,
        env="SAFIR_KAFKA_CLIENT_KEY",
        description=(
            "The path of the Strimzi-generated SSL client key file for the "
            "Kafka client."
        ),
    )

    kafka_broker_url: Optional[str] = Field(
        None,
        env="SAFIR_KAFKA_BROKER_URL",
        description=(
            "The URL of the Kafka broker without the scheme "
            "(e.g. ``localhost:9092``)."
        ),
    )

    schema_registry_url: Optional[AnyHttpUrl] = Field(
        None,
        env="SAFIR_SCHEMA_REGISTRY_URL",
        description="The URL of the Confluent Schema Registry.",
    )

    schema_suffix: str = Field(
        "",
        env="SAFIR_SCHEMA_SUFFIX",
        description=(
            "A suffix for Avro schema names / Schema Registry subject names "
            "for development and staging. Leave as an empty string for "
            "production."
        ),
    )

    schema_compatibility: Optional[SchemaCompatibilityEnum] = Field(
        None,
        env="SAFIR_SCHEMA_COMPATIBILITY",
        description=(
            "The Schema Registry subject compatibility setting to use for "
            "schemas registered by the app. Leave unset (i.e., the default of "
            "`None` to use the Schema Registry's default compatibility "
            "setting."
        ),
    )

    enable_ltd_events_kafka_topic: bool = Field(
        True,
        env="ENABLE_LTD_EVENTS_KAFKA_TOPIC",
        description=(
            "Enable Kafka consumer for ltd_events_kafka_topic (ltd.events)."
        ),
    )

    ltd_events_kafka_topic: str = Field(
        "ltd.events",
        env="LTD_EVENTS_KAFKA_TOPIC",
        description=(
            "The name of the Kafka topic for messages produced by LTD Events."
        ),
    )

    enable_ingest_kafka_topic: bool = Field(
        True,
        env="ENABLE_OOK_INGEST_KAFKA_TOPIC",
        description=(
            "Enable Kafka consumer for ingest_kafka_topic (ook.ingest)."
        ),
    )

    ingest_kafka_topic: str = Field(
        "ook.ingest",
        env="OOK_INGEST_KAFKA_TOPIC",
        description="The name of the Kafka topic for the ingest queue.",
    )

    schema_root_dir: Path = Field(
        Path(__file__).parent / "avro_schemas",
        description=(
            "Directory containing Avro schemas managed directly by the app."
        ),
    )

    kafka_consumer_group_id: str = Field(
        "ook", env="OOK_GROUP_ID", description="Kafka consumer group ID."
    )

    algolia_app_id: Optional[str] = Field(
        None, env="ALGOLIA_APP_ID", description="The Algolia app ID"
    )

    algolia_api_key: Optional[SecretStr] = Field(
        None, env="ALGOLIA_API_KEY", description="The Algolia API key"
    )

    algolia_document_index_name: str = Field(
        "document_dev",
        env="ALGOLIA_DOCUMENT_INDEX",
        description="Name of the Algolia document index",
    )

    @validator("kafka_cluster_ca_path")
    def validate_kafka_cluster_ca_path(
        cls, v: ValueType, values: ValuesType
    ) -> ValueType:
        if values["kafka_protocol"] == "SSL" and v is None:
            raise ValueError(
                "SAFIR_KAFKA_CLUSTER_CA must be set if SAFIR_KAFKA_PROTOCOL "
                "is 'SSL'."
            )
        return v

    @validator("kafka_client_cert_path")
    def validate_kafka_client_cert_path(
        cls, v: ValueType, values: ValuesType
    ) -> ValueType:
        if values["kafka_protocol"] == "SSL" and v is None:
            raise ValueError(
                "SAFIR_KAFKA_CLIENT_CERT must be set if SAFIR_KAFKA_PROTOCOL "
                "is 'SSL'."
            )
        return v

    @validator("kafka_client_key_path")
    def validate_kafka_client_key_path(
        cls, v: ValueType, values: ValuesType
    ) -> ValueType:
        if values["kafka_protocol"] == "SSL" and v is None:
            raise ValueError(
                "SAFIR_KAFKA_CLIENT_KEY must be set if SAFIR_KAFKA_PROTOCOL "
                "is 'SSL'."
            )
        return v
