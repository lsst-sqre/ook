"""Pure status-transition engine for link checking.

The engine is deliberately free of I/O: it takes a link's prior state
plus the outcome of a single check and returns the next state. HTTP
checking, persistence, and configuration binding live in other layers.
"""

from __future__ import annotations

from datetime import timedelta
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

_MAX_BLOCKED_BACKOFF_DOUBLINGS = 30
"""Ceiling on the blocked-backoff doubling exponent.

Guards the ``timedelta`` multiplication against overflow for a link that
stays blocked indefinitely. The delay is capped at the broken-recheck
interval well before this many doublings, so this ceiling only bounds the
arithmetic, not the observable cadence.
"""


def _blocked_recheck_delay(
    ladder: RetryLadderConfig, blocked_count: int
) -> timedelta:
    """Compute the recheck delay for a blocked link, with backoff.

    The delay doubles with each additional consecutive blocked outcome,
    starting from the configured blocked-recheck interval, and is capped
    at the (slower) broken-recheck interval. A permanently blocked link
    therefore converges to the broken cadence rather than rechecking at
    the near-term blocked interval forever, while the first block still
    rechecks promptly (blocks tend to flap).

    Parameters
    ----------
    ladder
        The retry-ladder configuration supplying the base blocked
        interval and the cap.
    blocked_count
        The number of consecutive blocked outcomes so far (>= 1).

    Returns
    -------
    timedelta
        The delay until the next recheck.
    """
    base = ladder.blocked_recheck_interval
    # Never let the cap fall below the base, in case broken_recheck is
    # configured shorter than blocked_recheck.
    cap = max(ladder.broken_recheck_interval, base)
    exponent = min(blocked_count - 1, _MAX_BLOCKED_BACKOFF_DOUBLINGS)
    return min(base * (2**exponent), cap)


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
            consecutive_blocked_count=0,
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
            consecutive_blocked_count=0,
            status_code=outcome.status_code,
            redirect_status_code=outcome.redirect_status_code,
            redirect_url=outcome.redirect_url,
            error=None,
            next_check_at=None,
        )

    if outcome.is_bot_blocked or outcome.is_transient:
        # Both a bot-protection block and a transient server condition (a
        # persistent 429 rate limit or a 503 outage) are inconclusive:
        # the link may well be fine. Report ``blocked`` without discarding
        # or extending the failing→broken streak (so an inconclusive check
        # cannot push a link to broken nor reset progress toward it) and
        # preserve the last-OK marker. Count the consecutive blocked
        # outcomes (a dedicated counter, kept separate from the
        # failing→broken ladder) and back off the recheck cadence as they
        # accumulate, so a permanently blocked link converges to the slow
        # broken cadence instead of rechecking hourly forever.
        prior_blocked = (
            prior.consecutive_blocked_count if prior is not None else 0
        )
        blocked_count = prior_blocked + 1
        return LinkState(
            url=url,
            status=LinkStatus.blocked,
            checked_at=outcome.checked_at,
            last_ok_at=prior.last_ok_at if prior is not None else None,
            failing_since=prior.failing_since if prior is not None else None,
            failure_count=prior.failure_count if prior is not None else 0,
            consecutive_blocked_count=blocked_count,
            status_code=outcome.status_code,
            redirect_status_code=None,
            redirect_url=None,
            error=outcome.error,
            next_check_at=(
                outcome.checked_at
                + _blocked_recheck_delay(ladder, blocked_count)
            ),
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
        consecutive_blocked_count=0,
        status_code=outcome.status_code,
        redirect_status_code=None,
        redirect_url=None,
        error=outcome.error,
        next_check_at=next_check_at,
    )
