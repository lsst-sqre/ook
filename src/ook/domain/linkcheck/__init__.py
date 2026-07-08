"""Domain for external link checking."""

from ._engine import (
    canonicalize_url,
    evaluate_outcome,
    is_supported_url,
    normalize_origin_base_url,
)
from ._models import (
    CheckedUrlReport,
    CheckResult,
    CheckRunStatus,
    CheckUrlStatus,
    LinkCheckOutcome,
    LinkCheckReport,
    LinkState,
    LinkStatus,
    OriginLink,
    OriginPage,
    RetryLadderConfig,
    SubmittedUrl,
    UrlOccurrence,
    UrlRecord,
)

__all__ = [
    "CheckResult",
    "CheckRunStatus",
    "CheckUrlStatus",
    "CheckedUrlReport",
    "LinkCheckOutcome",
    "LinkCheckReport",
    "LinkState",
    "LinkStatus",
    "OriginLink",
    "OriginPage",
    "RetryLadderConfig",
    "SubmittedUrl",
    "UrlOccurrence",
    "UrlRecord",
    "canonicalize_url",
    "evaluate_outcome",
    "is_supported_url",
    "normalize_origin_base_url",
]
