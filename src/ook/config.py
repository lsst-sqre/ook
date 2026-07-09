"""Configuration definition."""

from __future__ import annotations

from datetime import timedelta

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings
from safir.kafka import KafkaConnectionSettings
from safir.logging import LogLevel, Profile
from safir.pydantic import EnvAsyncPostgresDsn, HumanTimedelta

import ook

__all__ = [
    "Configuration",
    "config",
]

_DEFAULT_LINKCHECK_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:100.0) Gecko/20100101 "
    f"Firefox/100.0 Ook-Linkcheck/{ook.__version__} "
    "(+https://github.com/lsst-sqre/ook)"
)
"""Default link-check User-Agent.

A browser-prefixed hybrid: Sphinx's own linkcheck builder ships the same
``Mozilla/5.0 ... Firefox/100.0`` prefix by default, establishing that this
posture is acceptable ecosystem precedent. The honest ``Ook-Linkcheck`` token
(carrying the running version and repo URL) keeps the checker identifiable and
allowlist-able, while the browser prefix clears Cloudflare zones that the bare
token did not.
"""


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

    linkcheck_kafka_topic: str = Field(
        "ook.linkcheck",
        validation_alias="OOK_LINKCHECK_KAFKA_TOPIC",
        description=(
            "The name of the Kafka topic for link-check execution requests."
        ),
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

    linkcheck_request_timeout: HumanTimedelta = Field(
        timedelta(seconds=30),
        validation_alias="OOK_LINKCHECK_REQUEST_TIMEOUT",
        description=("Total timeout applied to each link-check HTTP request."),
    )

    linkcheck_max_concurrency: int = Field(
        10,
        ge=1,
        validation_alias="OOK_LINKCHECK_MAX_CONCURRENCY",
        description=(
            "Maximum number of concurrent link-check HTTP requests"
            " across all hosts."
        ),
    )

    linkcheck_host_interval: HumanTimedelta = Field(
        timedelta(seconds=1),
        validation_alias="OOK_LINKCHECK_HOST_INTERVAL",
        description=(
            "Minimum politeness interval between link-check requests to"
            " the same host."
        ),
    )

    linkcheck_user_agent: str = Field(
        _DEFAULT_LINKCHECK_USER_AGENT,
        validation_alias="OOK_LINKCHECK_USER_AGENT",
        description=(
            "User-Agent header sent on every link-check HTTP request"
            " (HEAD, GET fallback, and redirect hops). Defaults to a"
            " browser-prefixed hybrid carrying the running Ook version and"
            " repo URL so the checker stays identifiable while clearing"
            " bot-protection zones that block the bare token."
        ),
    )

    linkcheck_freshness_ttl: HumanTimedelta = Field(
        timedelta(hours=24),
        validation_alias="OOK_LINKCHECK_FRESHNESS_TTL",
        description=(
            "Age below which a URL's stored check result is considered"
            " fresh. URLs submitted with a fresh result are not"
            " rechecked; their cached status is reported immediately."
        ),
    )

    linkcheck_max_urls_per_check: int = Field(
        1000,
        ge=1,
        validation_alias="OOK_LINKCHECK_MAX_URLS_PER_CHECK",
        description=(
            "Maximum number of unique canonical URLs accepted in a"
            " single link-check submission."
        ),
    )

    linkcheck_broken_threshold: HumanTimedelta = Field(
        timedelta(hours=48),
        validation_alias="OOK_LINKCHECK_BROKEN_THRESHOLD",
        description=(
            "Minimum span of consecutive failures before a previously-OK"
            " link is declared broken instead of failing."
        ),
    )

    linkcheck_broken_min_attempts: int = Field(
        3,
        ge=1,
        validation_alias="OOK_LINKCHECK_BROKEN_MIN_ATTEMPTS",
        description=(
            "Minimum number of consecutive failed attempts before a"
            " previously-OK link is declared broken instead of failing."
        ),
    )

    linkcheck_recheck_intervals: tuple[HumanTimedelta, ...] = Field(
        (
            timedelta(hours=1),
            timedelta(hours=4),
            timedelta(hours=24),
            timedelta(hours=48),
        ),
        min_length=1,
        validation_alias="OOK_LINKCHECK_RECHECK_INTERVALS",
        description=(
            "Delays until the next recheck of a failing link, indexed by"
            " the number of consecutive failures so far. The last"
            " interval repeats when the failure streak outlasts this"
            " schedule."
        ),
    )

    linkcheck_broken_recheck_interval: HumanTimedelta = Field(
        timedelta(hours=24),
        validation_alias="OOK_LINKCHECK_BROKEN_RECHECK_INTERVAL",
        description=(
            "Delay until the next recheck of a broken link. Broken links"
            " are revisited at this slow cadence so a since-fixed link can"
            " heal back to ok/redirected without waiting to be"
            " resubmitted."
        ),
    )

    linkcheck_blocked_recheck_interval: HumanTimedelta = Field(
        timedelta(hours=1),
        validation_alias="OOK_LINKCHECK_BLOCKED_RECHECK_INTERVAL",
        description=(
            "Delay until the next recheck of a bot-blocked link. A block"
            " is inconclusive and tends to flap, so blocked links are"
            " revisited at this near-term cadence to re-verify."
        ),
    )

    linkcheck_check_retention: HumanTimedelta = Field(
        timedelta(days=30),
        validation_alias="OOK_LINKCHECK_CHECK_RETENTION",
        description=(
            "Age beyond which link-check submission records are purged"
            " by the scheduled linkcheck-recheck maintenance command."
        ),
    )

    intersphinx_ttl: HumanTimedelta = Field(
        timedelta(hours=1),
        validation_alias="OOK_INTERSPHINX_TTL",
        description=(
            "Freshness TTL for cached intersphinx inventories. An inventory"
            " fetched within this window is served as a fresh cache hit; an"
            " older one is served stale on the request path while the"
            " background refresh job revalidates it."
        ),
    )

    intersphinx_negative_ttl: HumanTimedelta = Field(
        timedelta(minutes=5),
        validation_alias="OOK_INTERSPHINX_NEGATIVE_TTL",
        description=(
            "Negative-cache TTL for cold-miss intersphinx inventory fetch"
            " failures. When an upstream fetch fails on a cold miss the"
            " failure is cached for this window; a repeat request inside it"
            " returns the error without re-contacting upstream. After the"
            " window a new request re-fetches the origin."
        ),
    )

    intersphinx_active_window: HumanTimedelta = Field(
        timedelta(days=30),
        validation_alias="OOK_INTERSPHINX_ACTIVE_WINDOW",
        description=(
            "Active window for the intersphinx refresh job. The scheduled"
            " refresh only revalidates cached inventories requested by a"
            " client within this window; inventories last requested longer"
            " ago are skipped (not deleted) until a new request reactivates"
            " them."
        ),
    )

    slack_webhook: SecretStr | None = Field(
        None,
        validation_alias="OOK_SLACK_WEBHOOK",
        description=(
            "Slack webhook for alerts. If set, alerts will be posted to this "
            "Slack webhook"
        ),
    )

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
