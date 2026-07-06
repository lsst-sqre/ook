"""Domain for external link checking."""

from ._engine import evaluate_outcome, is_supported_url
from ._models import (
    CheckResult,
    LinkCheckOutcome,
    LinkState,
    LinkStatus,
    RetryLadderConfig,
    UrlOccurrence,
)

__all__ = [
    "CheckResult",
    "LinkCheckOutcome",
    "LinkState",
    "LinkStatus",
    "RetryLadderConfig",
    "UrlOccurrence",
    "evaluate_outcome",
    "is_supported_url",
]
