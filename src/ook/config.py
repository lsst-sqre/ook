"""Configuration definition."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional, Any, Mapping, TypeVar
from ssl import SSLContext
from kafkit.ssl import create_ssl_context

from safir.logging import LogLevel, Profile
from pydantic import (
    AnyHttpUrl,
    BaseSettings,
    Field,
    SecretStr,
    validator,
    DirectoryPath,
    FilePath,
)

__all__ = ["Configuration", "config"]


ValuesType = Mapping[str, Any]
ValueType = TypeVar("ValueType")


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
        env="KAFKA_BOOTSTRAP_SERVERS",
        description=(
            "A comma-separated list of Kafka brokers to connect to. "
            "This should be a list of hostnames or IP addresses, "
            "each optionally followed by a port number, separated by "
            "commas. "
            "For example: `kafka-1:9092,kafka-2:9092,kafka-3:9092`."
        ),
    )

    security_protocol: KafkaSecurityProtocol = Field(
        KafkaSecurityProtocol.PLAINTEXT,
        env="KAFKA_SECURITY_PROTOCOL",
        description="The security protocol to use when connecting to Kafka.",
    )

    cert_temp_dir: DirectoryPath | None = Field(
        None,
        env="KAFKA_CERT_TEMP_DIR",
        description=(
            "Temporary writable directory for concatenating certificates."
        ),
    )

    cluster_ca_path: FilePath | None = Field(
        None,
        title="Path to CA certificate file",
        env="KAFKA_SSL_CLUSTER_CAFILE",
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
        env="KAFKA_SSL_CLIENT_CAFILE",
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
        env="KAFKA_SSL_CLIENT_CERTFILE",
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
        env="KAFKA_SSL_CLIENT_KEYFILE",
        description=(
            "The path to the client key file to use for authentication. "
            "This is only needed if the broker is configured to require "
            "SSL client authentication."
        ),
    )

    client_key_password: SecretStr | None = Field(
        None,
        title="Password for client key file",
        env="KAFKA_SSL_CLIENT_KEY_PASSWORD",
        description=(
            "The password to use for decrypting the client key file. "
            "This is only needed if the client key file is encrypted."
        ),
    )

    sasl_mechanism: KafkaSaslMechanism | None = Field(
        KafkaSaslMechanism.PLAIN,
        title="SASL mechanism",
        env="KAFKA_SASL_MECHANISM",
        description=(
            "The SASL mechanism to use for authentication. "
            "This is only needed if SASL authentication is enabled."
        ),
    )

    sasl_username: str | None = Field(
        None,
        title="SASL username",
        env="KAFKA_SASL_USERNAME",
        description=(
            "The username to use for SASL authentication. "
            "This is only needed if SASL authentication is enabled."
        ),
    )

    sasl_password: SecretStr | None = Field(
        None,
        title="SASL password",
        env="KAFKA_SASL_PASSWORD",
        description=(
            "The password to use for SASL authentication. "
            "This is only needed if SASL authentication is enabled."
        ),
    )

    @property
    def ssl_context(self) -> SSLContext | None:
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

        # For type checking
        assert self.client_cert_path is not None
        assert self.cluster_ca_path is not None
        assert self.client_key_path is not None

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
            if client_ca.endswith("\n"):
                sep = ""
            else:
                sep = "\n"
            new_client_cert = sep.join([client_cert, client_ca])
            new_client_cert_path = Path(self.cert_temp_dir) / "client.crt"
            new_client_cert_path.write_text(new_client_cert)
            client_cert_path = Path(new_client_cert_path)

        return create_ssl_context(
            cluster_ca_path=Path(self.cluster_ca_path),
            client_cert_path=client_cert_path,
            client_key_path=Path(self.client_key_path),
        )


class Configuration(BaseSettings):
    """Configuration for ook."""

    name: str = Field(
        "ook",
        env="SAFIR_NAME",
        description=("The application's name"),
    )

    profile: Profile = Field(
        Profile.production,
        env="SAFIR_PROFILE",
        description="Application logging profile: 'development' or 'production'.",
    )

    path_prefix: str = Field(
        "/squarebot",
        title="API URL path prefix",
        env="SAFIR_PATH_PREFIX",
        description=(
            "The URL prefix where the application's externally-accessible "
            "endpoints are hosted."
        ),
    )

    environment_url: AnyHttpUrl = Field(
        ...,
        title="Base URL of the environment",
        env="SAFIR_ENVIRONMENT_URL",
        description=(
            "The base URL of the environment where the application is hosted."
        ),
    )

    kafka: KafkaConnectionSettings = Field(
        default_factory=KafkaConnectionSettings,
        description="Kafka connection configuration.",
    )

    registry_url: AnyHttpUrl = Field(
        env="OOK_REGISTRY_URL", title="Schema Registry URL"
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

    github_app_id: Optional[str] = Field(None, env="OOK_GITHUB_APP_ID")
    """The GitHub App ID, as determined by GitHub when setting up a GitHub
    App.
    """

    github_webhook_secret: Optional[SecretStr] = Field(
        None, env="OOK_GITHUB_WEBHOOK_SECRET"
    )
    """The GitHub app's webhook secret, as set when the App was created. See
    https://docs.github.com/en/developers/webhooks-and-events/webhooks/securing-your-webhooks
    """

    github_app_private_key: Optional[SecretStr] = Field(
        None, env="OOK_GITHUB_APP_PRIVATE_KEY"
    )
    """The GitHub app private key. See
    https://docs.github.com/en/developers/apps/building-github-apps/authenticating-with-github-apps#generating-a-private-key
    """

    enable_github_app: bool = Field(True, env="OOK_ENABLE_GITHUB_APP")
    """Toggle to enable GitHub App functionality.

    If configurations required to function as a GitHub App are not set,
    this configuration is automatically toggled to False. It also also be
    manually toggled to False if necessary.
    """

    @validator("github_webhook_secret", "github_app_private_key", pre=True)
    def validate_none_secret(
        cls, v: Optional[SecretStr]
    ) -> Optional[SecretStr]:
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

    @validator("enable_github_app")
    def validate_github_app(cls, v: bool, values: Mapping[str, Any]) -> bool:
        """Validate ``enable_github_app`` by ensuring that other GitHub
        configurations are also set.
        """
        if v is False:
            # Allow the GitHub app to be disabled regardless of other
            # configurations.
            return False

        if (
            (values.get("github_app_private_key") is None)
            or (values.get("github_webhook_secret") is None)
            or (values.get("github_app_id") is None)
        ):
            return False

        return True


config = Configuration()
"""Configuration instance."""
