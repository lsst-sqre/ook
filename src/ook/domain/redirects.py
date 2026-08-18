"""Shared HTTP redirect-following policy.

Ook follows redirects by hand in two places — the link checker
(`ook.services.linkcheck`) and the intersphinx inventory cache
(`ook.services.intersphinx`) — because each hop's target has to pass an
SSRF guard before it is fetched, which ``httpx``'s own redirect handling
cannot do. Both loops therefore need the same three policy decisions:
which status codes count as a redirect, how many hops are too many, and
when a chain is permanent enough that the requester should update the URL
it asked for.

Those decisions live here so the two loops cannot drift apart. They are
user-visible in different vocabularies — the checks API's
``LinkStatus.redirected`` and the inventory endpoint's
``X-Ook-Inventory-Permanent-Redirect`` header — and an identical chain
must be classified identically by both.
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = [
    "MAX_REDIRECTS",
    "PERMANENT_REDIRECT_CODES",
    "REDIRECT_CODES",
    "TooManyRedirectsError",
    "is_permanent_chain",
]


REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
"""HTTP status codes followed as redirects, when paired with a
``Location`` header.
"""

PERMANENT_REDIRECT_CODES = frozenset({301, 308})
"""Redirect status codes meaning the requested URL itself has moved, so
the requester should update the URL it asked for.
"""

MAX_REDIRECTS = 20
"""Maximum number of redirect hops followed before giving up, so a
redirect loop terminates.
"""


class TooManyRedirectsError(Exception):
    """A redirect chain exceeded `MAX_REDIRECTS` hops.

    Raised and caught by the link checker's hop loop, and defined here so
    that a service whose own hop loop needs to catch this failure catches
    the same class rather than growing a second one. The intersphinx cache
    does not: it reports every way a fetch can fail through one error of
    its own, and nothing on its paths catches this failure apart from the
    rest.
    """


def is_permanent_chain(hops: Iterable[int]) -> bool:
    """Report whether every hop of a redirect chain was permanent.

    A chain counts as permanent only when *every* hop is a 301 or 308: a
    single temporary hop means the terminal URL is not a stable
    replacement for the requested one, so the whole chain is temporary.

    An empty chain is vacuously permanent. Callers distinguish "did not
    redirect" from "redirected" before asking, so that answer is never the
    one they report.

    Parameters
    ----------
    hops
        Status codes of the redirect responses followed, in order.

    Returns
    -------
    bool
        True when no hop was a temporary redirect.
    """
    return all(code in PERMANENT_REDIRECT_CODES for code in hops)
