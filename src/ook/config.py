"""Configuration definition."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kafkit.settings import KafkaConnectionSettings
from pydantic import AnyHttpUrl, BaseSettings, Field, SecretStr, validator
from safir.logging import LogLevel, Profile

__all__ = ["Configuration", "config"]


class Configuration(BaseSettings):
    """Configuration for ook."""

    name: str = Field(
        "ook",
        env="SAFIR_NAME",
        description="The application's name",
    )

    profile: Profile = Field(
        Profile.production,
        env="SAFIR_PROFILE",
        description=(
            "Application logging profile: 'development' or 'production'."
        ),
    )

    log_level: LogLevel = Field(
        LogLevel.INFO,
        title="Log level of the application's logger",
        env="SAFIR_LOG_LEVEL",
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

    algolia_app_id: str | None = Field(
        None, env="ALGOLIA_APP_ID", description="The Algolia app ID"
    )

    algolia_api_key: SecretStr | None = Field(
        None, env="ALGOLIA_API_KEY", description="The Algolia API key"
    )

    algolia_document_index_name: str = Field(
        "document_dev",
        env="ALGOLIA_DOCUMENT_INDEX",
        description="Name of the Algolia document index",
    )

    github_app_id: str | None = Field(None, env="OOK_GITHUB_APP_ID")
    """The GitHub App ID, as determined by GitHub when setting up a GitHub
    App.
    """

    github_webhook_secret: SecretStr | None = Field(
        None, env="OOK_GITHUB_WEBHOOK_SECRET"
    )
    """The GitHub app's webhook secret, as set when the App was created. See
    https://docs.github.com/en/developers/webhooks-and-events/webhooks/securing-your-webhooks
    """

    github_app_private_key: SecretStr | None = Field(
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
