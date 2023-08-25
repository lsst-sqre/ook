"""Configuration definition."""

from __future__ import annotations

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
        "/ook",
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

    subject_suffix: str = Field(
        "",
        title="Schema subject name suffix",
        env="OOK_SUBJECT_SUFFIX",
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
        env="OOK_SUBJECT_COMPATIBILITY",
        description=(
            "Compatibility level to apply to Schema Registry subjects. Use "
            "NONE for testing and development, but prefer FORWARD_TRANSITIVE "
            "for production."
        ),
    )

    enable_kafka_consumer: bool = Field(
        True,
        env="OOK_ENABLE_CONSUMER",
        description="Enable Kafka consumer.",
    )

    ingest_kafka_topic: str = Field(
        "ook.ingest",
        env="OOK_INGEST_KAFKA_TOPIC",
        description="The name of the Kafka topic for the ingest queue.",
    )

    kafka_consumer_group_id: str = Field(
        "ook", env="OOK_GROUP_ID", description="Kafka consumer group ID."
    )

    algolia_app_id: str = Field(
        env="ALGOLIA_APP_ID", description="The Algolia app ID"
    )

    algolia_api_key: SecretStr = Field(
        env="ALGOLIA_API_KEY", description="The Algolia API key"
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

    github_app_private_key: SecretStr | None = Field(
        None, env="OOK_GITHUB_APP_PRIVATE_KEY"
    )
    """The GitHub app private key. See
    https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps
    """

    @validator("github_app_private_key", pre=True)
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
