"""Pure status-transition engine for link checking.

The engine is deliberately free of I/O: it takes a link's prior state
plus the outcome of a single check and returns the next state. HTTP
checking, persistence, and configuration binding live in other layers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urldefrag, urlsplit, urlunsplit

from ._models import (
    CheckResult,
    LinkCheckOutcome,
    LinkState,
    LinkStatus,
    RetryLadderConfig,
)

if TYPE_CHECKING:
    from datetime import datetime

__all__ = [
    "canonicalize_url",
    "evaluate_outcome",
    "is_supported_url",
    "normalize_origin_base_url",
]

_SUPPORTED_SCHEMES = frozenset({"http", "https"})
"""URL schemes the link checker is able to check."""

_PERMANENT_REDIRECT_CODES = frozenset({301, 308})
"""Redirect status codes indicating the source should be updated."""


def canonicalize_url(url: str) -> str:
    """Canonicalize a URL for link checking by stripping its fragment.

    Fragments are client-side and never affect what a server returns, so
    all fragment variants of a URL share one health record.

    Parameters
    ----------
    url
        The URL to canonicalize.

    Returns
    -------
    str
        The URL without its fragment.
    """
    return urldefrag(url).url


def normalize_origin_base_url(url: str) -> str:
    """Normalize an origin's base URL to its canonical form.

    An origin identifies the website a link check is submitted for
    (e.g. ``https://documenteer.lsst.io``). Path-bearing bases are
    allowed (e.g. ``https://rsp.lsst.io/guides``). Normalization
    lowercases the host and strips any trailing slash so equivalent
    spellings map to one origin.

    Parameters
    ----------
    url
        The origin base URL to normalize.

    Returns
    -------
    str
        The normalized origin base URL.

    Raises
    ------
    ValueError
        Raised if the URL is not an absolute http(s) URL with a host,
        or if it carries a query or fragment.
    """
    parts = urlsplit(url)
    if parts.scheme not in _SUPPORTED_SCHEMES:
        raise ValueError(
            f"Origin base URL {url!r} must use the http or https scheme."
        )
    if not parts.netloc:
        raise ValueError(f"Origin base URL {url!r} must include a host.")
    if parts.query or parts.fragment:
        raise ValueError(
            f"Origin base URL {url!r} must not have a query or fragment."
        )
    return urlunsplit(
        (parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), "", "")
    )


def is_supported_url(url: str) -> bool:
    """Determine whether a URL can be checked by the link checker.

    Only well-formed ``http`` and ``https`` URLs with a host are
    supported. Other schemes (``mailto``, ``ftp``, ...) and malformed
    URLs are classified as unsupported.

    Parameters
    ----------
    url
        The URL to classify.

    Returns
    -------
    bool
        `True` if the URL can be checked, `False` otherwise.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in _SUPPORTED_SCHEMES and bool(parts.netloc)


def evaluate_outcome(
    *,
    url: str,
    prior: LinkState | None,
    outcome: LinkCheckOutcome,
    ladder: RetryLadderConfig,
) -> LinkState:
    """Compute a link's next state from its prior state and a check
    outcome.

    This is a pure function: it performs no I/O and derives the next
    state entirely from its arguments.

    Parameters
    ----------
    url
        The checked URL.
    prior
        The link's state before this check, or None if the link has
        never been checked.
    outcome
        The outcome of the check that was just performed.
    ladder
        Retry-ladder thresholds, supplied by the caller (bound to
        application configuration in the service layer).

    Returns
    -------
    LinkState
        The link's next state.
    """
    if outcome.result is CheckResult.unsupported:
        # Unsupported URLs are never checked again by the ladder; they
        # only change status if the URL itself changes.
        return LinkState(
            url=url,
            status=LinkStatus.unsupported,
            checked_at=outcome.checked_at,
            last_ok_at=prior.last_ok_at if prior is not None else None,
            failing_since=None,
            failure_count=0,
            status_code=outcome.status_code,
            redirect_status_code=None,
            redirect_url=None,
            error=outcome.error,
            next_check_at=None,
        )

    if outcome.result is CheckResult.success:
        # Permanent redirects mean the link works but the source should
        # be updated to the recorded final location. Temporary
        # redirects resolve OK, with redirect metadata retained.
        if outcome.redirect_status_code in _PERMANENT_REDIRECT_CODES:
            status = LinkStatus.redirected
        else:
            status = LinkStatus.ok
        return LinkState(
            url=url,
            status=status,
            checked_at=outcome.checked_at,
            last_ok_at=outcome.checked_at,
            failing_since=None,
            failure_count=0,
            status_code=outcome.status_code,
            redirect_status_code=outcome.redirect_status_code,
            redirect_url=outcome.redirect_url,
            error=None,
            next_check_at=None,
        )

    if outcome.is_bot_blocked:
        # A bot-protection block is inconclusive: the link may well be
        # fine. Report ``blocked`` without discarding or extending the
        # failing→broken streak (so a block cannot push a link to broken
        # nor reset progress toward it), preserve the last-OK marker, and
        # schedule a near-term recheck because blocks tend to flap.
        return LinkState(
            url=url,
            status=LinkStatus.blocked,
            checked_at=outcome.checked_at,
            last_ok_at=prior.last_ok_at if prior is not None else None,
            failing_since=prior.failing_since if prior is not None else None,
            failure_count=prior.failure_count if prior is not None else 0,
            status_code=outcome.status_code,
            redirect_status_code=None,
            redirect_url=None,
            error=outcome.error,
            next_check_at=outcome.checked_at + ladder.blocked_recheck_interval,
        )

    # Failure path: extend (or start) the consecutive-failure streak.
    last_ok_at = prior.last_ok_at if prior is not None else None
    if prior is not None and prior.failing_since is not None:
        failing_since = prior.failing_since
        failure_count = prior.failure_count + 1
    else:
        failing_since = outcome.checked_at
        failure_count = 1

    if last_ok_at is None:
        # A link never seen OK is broken immediately: a brand-new
        # broken link is most likely an authoring error.
        status = LinkStatus.broken
    else:
        streak_span = outcome.checked_at - failing_since
        ladder_exhausted = (
            failure_count >= ladder.min_attempts
            and streak_span >= ladder.broken_threshold
        )
        status = LinkStatus.broken if ladder_exhausted else LinkStatus.failing

    next_check_at: datetime | None = None
    if status is LinkStatus.failing:
        interval_index = min(
            failure_count - 1, len(ladder.recheck_intervals) - 1
        )
        next_check_at = (
            outcome.checked_at + ladder.recheck_intervals[interval_index]
        )
    else:
        # Broken links are revisited at a slow cadence so a since-fixed
        # link heals back to ok/redirected via the success path without
        # waiting to be resubmitted.
        next_check_at = outcome.checked_at + ladder.broken_recheck_interval

    return LinkState(
        url=url,
        status=status,
        checked_at=outcome.checked_at,
        last_ok_at=last_ok_at,
        failing_since=failing_since,
        failure_count=failure_count,
        status_code=outcome.status_code,
        redirect_status_code=None,
        redirect_url=None,
        error=outcome.error,
        next_check_at=next_check_at,
    )
