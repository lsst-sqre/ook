"""Configuration definition."""

from __future__ import annotations

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings
from safir.kafka import KafkaConnectionSettings
from safir.logging import LogLevel, Profile
from safir.pydantic import EnvAsyncPostgresDsn

__all__ = [
    "Configuration",
    "config",
]


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

    database_url: EnvAsyncPostgresDsn = Field(
        ...,
        validation_alias="OOK_DATABASE_URL",
        description="Database URL.",
    )

    database_password: SecretStr = Field(
        ...,
        validation_alias="OOK_DATABASE_PASSWORD",
        description="Database password.",
    )

    kafka: KafkaConnectionSettings = Field(
        default_factory=KafkaConnectionSettings,
        description="Kafka connection configuration.",
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
