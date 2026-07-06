"""Domain for external link checking."""

from ._engine import evaluate_outcome, is_supported_url
from ._models import (
    CheckResult,
    LinkCheckOutcome,
    LinkState,
    LinkStatus,
    RetryLadderConfig,
)

__all__ = [
    "CheckResult",
    "LinkCheckOutcome",
    "LinkState",
    "LinkStatus",
    "RetryLadderConfig",
    "evaluate_outcome",
    "is_supported_url",
]
