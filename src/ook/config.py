"""Configuration definition."""

from __future__ import annotations

import ssl
from enum import Enum
from pathlib import Path

from pydantic import (
    AnyHttpUrl,
    DirectoryPath,
    Field,
    FilePath,
    SecretStr,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from safir.logging import LogLevel, Profile

__all__ = [
    "Configuration",
    "config",
    "KafkaConnectionSettings",
    "KafkaSecurityProtocol",
    "KafkaSaslMechanism",
    "KafkaConnectionSettings",
]


class KafkaSecurityProtocol(str, Enum):
    """Kafka security protocols understood by aiokafka."""

    PLAINTEXT = "PLAINTEXT"
    """Plain-text connection."""

    SSL = "SSL"
    """TLS-encrypted connection."""


class KafkaSaslMechanism(str, Enum):
    """Kafka SASL mechanisms understood by aiokafka."""

    PLAIN = "PLAIN"
    """Plain-text SASL mechanism."""

    SCRAM_SHA_256 = "SCRAM-SHA-256"
    """SCRAM-SHA-256 SASL mechanism."""

    SCRAM_SHA_512 = "SCRAM-SHA-512"
    """SCRAM-SHA-512 SASL mechanism."""


class KafkaConnectionSettings(BaseSettings):
    """Settings for connecting to Kafka."""

    bootstrap_servers: str = Field(
        ...,
        title="Kafka bootstrap servers",
        description=(
            "A comma-separated list of Kafka brokers to connect to. "
            "This should be a list of hostnames or IP addresses, "
            "each optionally followed by a port number, separated by "
            "commas. "
            "For example: ``kafka-1:9092,kafka-2:9092,kafka-3:9092``."
        ),
    )

    security_protocol: KafkaSecurityProtocol = Field(
        KafkaSecurityProtocol.PLAINTEXT,
        description="The security protocol to use when connecting to Kafka.",
    )

    cert_temp_dir: DirectoryPath | None = Field(
        None,
        description=(
            "Temporary writable directory for concatenating certificates."
        ),
    )

    cluster_ca_path: FilePath | None = Field(
        None,
        title="Path to CA certificate file",
        description=(
            "The path to the CA certificate file to use for verifying the "
            "broker's certificate. "
            "This is only needed if the broker's certificate is not signed "
            "by a CA trusted by the operating system."
        ),
    )

    client_ca_path: FilePath | None = Field(
        None,
        title="Path to client CA certificate file",
        description=(
            "The path to the client CA certificate file to use for "
            "authentication. "
            "This is only needed when the client certificate needs to be"
            "concatenated with the client CA certificate, which is common"
            "for Strimzi installations."
        ),
    )

    client_cert_path: FilePath | None = Field(
        None,
        title="Path to client certificate file",
        description=(
            "The path to the client certificate file to use for "
            "authentication. "
            "This is only needed if the broker is configured to require "
            "SSL client authentication."
        ),
    )

    client_key_path: FilePath | None = Field(
        None,
        title="Path to client key file",
        description=(
            "The path to the client key file to use for authentication. "
            "This is only needed if the broker is configured to require "
            "SSL client authentication."
        ),
    )

    client_key_password: SecretStr | None = Field(
        None,
        title="Password for client key file",
        description=(
            "The password to use for decrypting the client key file. "
            "This is only needed if the client key file is encrypted."
        ),
    )

    sasl_mechanism: KafkaSaslMechanism | None = Field(
        KafkaSaslMechanism.PLAIN,
        title="SASL mechanism",
        description=(
            "The SASL mechanism to use for authentication. "
            "This is only needed if SASL authentication is enabled."
        ),
    )

    sasl_username: str | None = Field(
        None,
        title="SASL username",
        description=(
            "The username to use for SASL authentication. "
            "This is only needed if SASL authentication is enabled."
        ),
    )

    sasl_password: SecretStr | None = Field(
        None,
        title="SASL password",
        description=(
            "The password to use for SASL authentication. "
            "This is only needed if SASL authentication is enabled."
        ),
    )

    model_config = SettingsConfigDict(
        env_prefix="KAFKA_", case_sensitive=False
    )

    @property
    def ssl_context(self) -> ssl.SSLContext | None:
        """An SSL context for connecting to Kafka with aiokafka, if the
        Kafka connection is configured to use SSL.
        """
        if (
            self.security_protocol != KafkaSecurityProtocol.SSL
            or self.cluster_ca_path is None
            or self.client_cert_path is None
            or self.client_key_path is None
        ):
            return None

        client_cert_path = Path(self.client_cert_path)

        if self.client_ca_path is not None:
            # Need to contatenate the client cert and CA certificates. This is
            # typical for Strimzi-based Kafka clusters.
            if self.cert_temp_dir is None:
                raise RuntimeError(
                    "KAFKIT_KAFKA_CERT_TEMP_DIR must be set when "
                    "a client CA certificate is provided."
                )
            client_ca = Path(self.client_ca_path).read_text()
            client_cert = Path(self.client_cert_path).read_text()
            sep = "" if client_ca.endswith("\n") else "\n"
            new_client_cert = sep.join([client_cert, client_ca])
            new_client_cert_path = Path(self.cert_temp_dir) / "client.crt"
            new_client_cert_path.write_text(new_client_cert)
            client_cert_path = Path(new_client_cert_path)

        # Create an SSL context on the basis that we're the client
        # authenticating the server (the Kafka broker).
        ssl_context = ssl.create_default_context(
            purpose=ssl.Purpose.SERVER_AUTH, cafile=str(self.cluster_ca_path)
        )
        # Add the certificates that the Kafka broker uses to authenticate us.
        ssl_context.load_cert_chain(
            certfile=str(client_cert_path), keyfile=str(self.client_key_path)
        )

        return ssl_context


class Configuration(BaseSettings):
    """Configuration for ook."""

    name: str = Field(
        "ook",
        validation_alias="SAFIR_NAME",
        description="The application's name",
    )

    profile: Profile = Field(
        Profile.production,
        validation_alias="SAFIR_PROFILE",
        description=(
            "Application logging profile: 'development' or 'production'."
        ),
    )

    log_level: LogLevel = Field(
        LogLevel.INFO,
        title="Log level of the application's logger",
        validation_alias="SAFIR_LOG_LEVEL",
    )

    path_prefix: str = Field(
        "/ook",
        title="API URL path prefix",
        validation_alias="SAFIR_PATH_PREFIX",
        description=(
            "The URL prefix where the application's externally-accessible "
            "endpoints are hosted."
        ),
    )

    environment_url: AnyHttpUrl = Field(
        ...,
        title="Base URL of the environment",
        validation_alias="SAFIR_ENVIRONMENT_URL",
        description=(
            "The base URL of the environment where the application is hosted."
        ),
    )

    kafka: KafkaConnectionSettings = Field(
        default_factory=KafkaConnectionSettings,
        description="Kafka connection configuration.",
    )

    registry_url: AnyHttpUrl = Field(
        validation_alias="OOK_REGISTRY_URL", title="Schema Registry URL"
    )

    subject_suffix: str = Field(
        "",
        title="Schema subject name suffix",
        validation_alias="OOK_SUBJECT_SUFFIX",
        description=(
            "Suffix to add to Schema Registry suffix names. This is useful "
            "when deploying for testing/staging and you do not "
            "want to affect the production subject and its "
            "compatibility lineage."
        ),
    )

    # TODO convert to enum?
    subject_compatibility: str = Field(
        "FORWARD_TRANSITIVE",
        title="Schema subject compatibility",
        validation_alias="OOK_SUBJECT_COMPATIBILITY",
        description=(
            "Compatibility level to apply to Schema Registry subjects. Use "
            "NONE for testing and development, but prefer FORWARD_TRANSITIVE "
            "for production."
        ),
    )

    enable_kafka_consumer: bool = Field(
        True,
        validation_alias="OOK_ENABLE_CONSUMER",
        description="Enable Kafka consumer.",
    )

    ingest_kafka_topic: str = Field(
        "ook.ingest",
        validation_alias="OOK_INGEST_KAFKA_TOPIC",
        description="The name of the Kafka topic for the ingest queue.",
    )

    kafka_consumer_group_id: str = Field(
        "ook",
        validation_alias="OOK_GROUP_ID",
        description="Kafka consumer group ID.",
    )

    algolia_app_id: str = Field(
        validation_alias="ALGOLIA_APP_ID", description="The Algolia app ID"
    )

    algolia_api_key: SecretStr = Field(
        validation_alias="ALGOLIA_API_KEY", description="The Algolia API key"
    )

    algolia_document_index_name: str = Field(
        "document_dev",
        validation_alias="ALGOLIA_DOCUMENT_INDEX",
        description="Name of the Algolia document index",
    )

    github_app_id: int | None = Field(
        None, validation_alias="OOK_GITHUB_APP_ID"
    )
    """The GitHub App ID, as determined by GitHub when setting up a GitHub
    App.
    """

    github_app_private_key: SecretStr | None = Field(
        None, validation_alias="OOK_GITHUB_APP_PRIVATE_KEY"
    )
    """The GitHub app private key. See
    https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps
    """

    @field_validator("github_app_private_key", mode="before")
    @classmethod
    def validate_none_secret(cls, v: SecretStr | None) -> SecretStr | None:
        """Validate a SecretStr setting which may be "None" that is intended
        to be `None`.

        This is useful for secrets generated from 1Password or environment
        variables where the value cannot be null.
        """
        if v is None:
            return v
        elif isinstance(v, str):
            if v.strip().lower() == "none":
                return None
            else:
                return v
        else:
            raise ValueError(f"Value must be None or a string: {v!r}")


config = Configuration()
"""Configuration instance."""
