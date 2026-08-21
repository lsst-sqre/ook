"""Pure status-transition engine for link checking.

The engine is deliberately free of I/O: it takes a link's prior state
plus the outcome of a single check and returns the next state. HTTP
checking, persistence, and configuration binding live in other layers.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.parse import urldefrag, urlsplit, urlunsplit

from ook.domain.redirects import PERMANENT_REDIRECT_CODES

from ._models import (
    CheckResult,
    ContributedResult,
    LinkCheckOutcome,
    LinkState,
    LinkStatus,
    ResultSource,
    RetryLadderConfig,
)

if TYPE_CHECKING:
    from datetime import datetime

__all__ = [
    "canonicalize_url",
    "contributed_outcome",
    "evaluate_outcome",
    "is_supported_url",
    "normalize_origin_base_url",
]

_SUPPORTED_SCHEMES = frozenset({"http", "https"})
"""URL schemes the link checker is able to check."""

_SUCCESS_CODES = range(200, 300)
"""HTTP status codes counted as a successful resolution.

The same range the URL checker applies to its own responses, so a
contributed result is judged resolved on exactly the terms a server check
is.
"""

_BOT_BLOCKED_CODE = 403
"""HTTP status code treated as a bot block in a contributed result.

The URL checker only calls a 403 a block when the response carries
Cloudflare's own headers, because it holds the response. A contributing
client reports a status code, not a response — and it is re-checking a URL
Ook was already blocked from, so a 403 observed from its vantage point too
is the block persisting rather than a newly-discovered broken link.
"""

_TRANSIENT_CODES = frozenset({429, 503})
"""HTTP status codes treated as transient server conditions.

Matching the URL checker: a persistent rate limit or a server-side outage
says nothing about whether the link is broken, so it never escalates the
failing-to-broken ladder.
"""

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


def contributed_outcome(
    result: ContributedResult, *, repository: str, received_at: datetime
) -> LinkCheckOutcome:
    """Convert a client-contributed result into a check outcome.

    A contributed result describes what a client saw when it resolved the
    URL from its own vantage point. This translates that description into
    the same outcome shape `evaluate_outcome` consumes for Ook's own
    checks, so a contribution advances a URL's state by exactly the rules
    a server check does.

    The classification is deliberately coarser than the URL checker's,
    which reads the response's own headers: a client reports a status code,
    not a response. A 2xx resolves the URL; a 403 is inconclusive (the
    client was blocked in turn), as are the transient 429 and 503; anything
    else is a confirmed failure that advances the retry ladder.

    Parameters
    ----------
    result
        The result the client observed.
    repository
        The ``owner/name`` of the repository whose CI observed the result,
        from its verified OIDC token. This is what attributes the resulting
        state to the contributing vantage point.
    received_at
        The time the server received the contribution, which becomes the
        outcome's check time. The client's own ``date_checked`` is advisory:
        stamping the server's receipt time is what keeps freshness and the
        retry ladder measured on one clock a client cannot skew.

    Returns
    -------
    LinkCheckOutcome
        The outcome to evaluate against the URL's prior state.
    """
    if result.status_code is not None and result.status_code in _SUCCESS_CODES:
        return LinkCheckOutcome(
            date_checked=received_at,
            result=CheckResult.success,
            status_code=result.status_code,
            redirect_status_code=result.redirect_status_code,
            redirect_url=result.redirect_url,
            contributed_by=repository,
        )
    is_bot_blocked = result.status_code == _BOT_BLOCKED_CODE
    if result.error is not None:
        error = result.error
    elif result.status_code is None:
        error = "The contributed check received no response"
    elif is_bot_blocked:
        error = f"HTTP {result.status_code} (likely blocked by bot protection)"
    else:
        error = f"HTTP {result.status_code}"
    return LinkCheckOutcome(
        date_checked=received_at,
        result=CheckResult.failure,
        status_code=result.status_code,
        redirect_status_code=result.redirect_status_code,
        redirect_url=result.redirect_url,
        error=error,
        is_bot_blocked=is_bot_blocked,
        is_transient=result.status_code in _TRANSIENT_CODES,
        contributed_by=repository,
    )


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
    # The vantage point the outcome came from travels through every
    # transition path unchanged: a contributed outcome is evaluated exactly
    # like a server one, and only the attribution differs.
    result_source = (
        ResultSource.server
        if outcome.contributed_by is None
        else ResultSource.contribution
    )

    if outcome.result is CheckResult.unsupported:
        # Unsupported URLs are never checked again by the ladder; they
        # only change status if the URL itself changes.
        return LinkState(
            url=url,
            status=LinkStatus.unsupported,
            date_checked=outcome.date_checked,
            date_last_ok=prior.date_last_ok if prior is not None else None,
            date_failing_since=None,
            failure_count=0,
            consecutive_blocked_count=0,
            status_code=outcome.status_code,
            redirect_status_code=None,
            redirect_url=None,
            error=outcome.error,
            date_next_check=None,
            result_source=result_source,
            contributed_by=outcome.contributed_by,
        )

    if outcome.result is CheckResult.success:
        # Permanent redirects mean the link works but the source should
        # be updated to the recorded final location. Temporary
        # redirects resolve OK, with redirect metadata retained.
        if outcome.redirect_status_code in PERMANENT_REDIRECT_CODES:
            status = LinkStatus.redirected
        else:
            status = LinkStatus.ok
        return LinkState(
            url=url,
            status=status,
            date_checked=outcome.date_checked,
            date_last_ok=outcome.date_checked,
            date_failing_since=None,
            failure_count=0,
            consecutive_blocked_count=0,
            status_code=outcome.status_code,
            redirect_status_code=outcome.redirect_status_code,
            redirect_url=outcome.redirect_url,
            error=None,
            date_next_check=None,
            result_source=result_source,
            contributed_by=outcome.contributed_by,
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
            date_checked=outcome.date_checked,
            date_last_ok=prior.date_last_ok if prior is not None else None,
            date_failing_since=prior.date_failing_since
            if prior is not None
            else None,
            failure_count=prior.failure_count if prior is not None else 0,
            consecutive_blocked_count=blocked_count,
            status_code=outcome.status_code,
            redirect_status_code=None,
            redirect_url=None,
            error=outcome.error,
            date_next_check=(
                outcome.date_checked
                + _blocked_recheck_delay(ladder, blocked_count)
            ),
            result_source=result_source,
            contributed_by=outcome.contributed_by,
        )

    # Failure path: extend (or start) the consecutive-failure streak.
    date_last_ok = prior.date_last_ok if prior is not None else None
    if prior is not None and prior.date_failing_since is not None:
        date_failing_since = prior.date_failing_since
        failure_count = prior.failure_count + 1
    else:
        date_failing_since = outcome.date_checked
        failure_count = 1

    if date_last_ok is None:
        # A link never seen OK is broken immediately: a brand-new
        # broken link is most likely an authoring error.
        status = LinkStatus.broken
    else:
        streak_span = outcome.date_checked - date_failing_since
        ladder_exhausted = (
            failure_count >= ladder.min_attempts
            and streak_span >= ladder.broken_threshold
        )
        status = LinkStatus.broken if ladder_exhausted else LinkStatus.failing

    date_next_check: datetime | None = None
    if status is LinkStatus.failing:
        interval_index = min(
            failure_count - 1, len(ladder.recheck_intervals) - 1
        )
        date_next_check = (
            outcome.date_checked + ladder.recheck_intervals[interval_index]
        )
    else:
        # Broken links are revisited at a slow cadence so a since-fixed
        # link heals back to ok/redirected via the success path without
        # waiting to be resubmitted.
        date_next_check = outcome.date_checked + ladder.broken_recheck_interval

    return LinkState(
        url=url,
        status=status,
        date_checked=outcome.date_checked,
        date_last_ok=date_last_ok,
        date_failing_since=date_failing_since,
        failure_count=failure_count,
        consecutive_blocked_count=0,
        status_code=outcome.status_code,
        redirect_status_code=None,
        redirect_url=None,
        error=outcome.error,
        date_next_check=date_next_check,
        result_source=result_source,
        contributed_by=outcome.contributed_by,
    )
