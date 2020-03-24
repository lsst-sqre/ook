"""Configuration definition."""

__all__ = ["Configuration"]

from enum import Enum

from pydantic import BaseSettings, Field


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
