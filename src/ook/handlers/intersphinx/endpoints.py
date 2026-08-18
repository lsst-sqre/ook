"""Endpoints for the /ook/intersphinx APIs."""

import hashlib
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response
from safir.datetime import isodatetime

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency
from ook.domain.intersphinx import IntersphinxInventory, InventoryCacheStatus
from ook.exceptions import UpstreamInventoryError

router = APIRouter(
    prefix=f"{config.path_prefix}/intersphinx", tags=["intersphinx"]
)
"""FastAPI router for all intersphinx inventory cache handlers."""

PERMANENT_REDIRECT_HEADER = "X-Ook-Inventory-Permanent-Redirect"
"""Header naming the URL a permanently-moved inventory now lives at."""

PERMANENT_REDIRECT_HEADER_SPEC = {
    PERMANENT_REDIRECT_HEADER: {
        "description": (
            "The URL this inventory's origin URL permanently redirects"
            " to. Present only when every hop of the redirect chain was"
            " permanent."
        ),
        "schema": {"type": "string", "format": "uri"},
    }
}
"""OpenAPI ``headers`` entry for the permanent-redirect header.

Merged into `INVENTORY_CACHE_HEADERS_SPEC`, which is what the responses
reference, so the header is documented once for every shape that carries
it.
"""

MAX_PERMANENT_REDIRECT_URL_LENGTH = 2048
"""Longest resolved URL that may be echoed in the permanent-redirect header.

The value is upstream-controlled and stored unbounded, while httpx only
caps a URL at 65,535 characters — far beyond the header buffers of a
typical ingress (ingress-nginx defaults to a 4k/8k ``proxy_buffer_size``).
A multi-kilobyte header would turn every cache hit for that row into an
ingress-level 502 that Ook itself logs as a successful serve, so anything
past this sanity bound is dropped.
"""

DATE_FETCHED_HEADER = "X-Ook-Inventory-Date-Fetched"
"""Header naming when Ook last confirmed this inventory with its origin."""

DATE_FETCHED_HEADER_SPEC = {
    DATE_FETCHED_HEADER: {
        "description": (
            "When Ook last confirmed this inventory with its origin, as an"
            " RFC 3339 UTC timestamp. Absent when the cached row records no"
            " fetch at all."
        ),
        "schema": {"type": "string", "format": "date-time"},
    }
}
"""OpenAPI ``headers`` entry for the date-fetched header.

Merged into `INVENTORY_CACHE_HEADERS_SPEC`, which is what the responses
reference, so the header is documented once for every shape that carries
it.
"""

CACHE_STATUS_HEADER = "X-Ook-Inventory-Cache-Status"
"""Header saying how this response's inventory was obtained."""

CACHE_STATUS_HEADER_SPEC = {
    CACHE_STATUS_HEADER: {
        "description": (
            "How Ook obtained the inventory this response describes:"
            " ``hit`` (served from a cached copy fetched within the"
            " freshness TTL), ``stale`` (served from a cached copy fetched"
            " longer ago than the TTL and retained for availability), or"
            " ``miss`` (not cached, so the origin was fetched"
            " synchronously to answer this request). Always present."
        ),
        "schema": {
            "type": "string",
            "enum": [status.value for status in InventoryCacheStatus],
        },
    }
}
"""OpenAPI ``headers`` entry for the cache-status header.

The documented values are generated from `InventoryCacheStatus` itself, so
a member added to (or renamed in) the enum the service reports from cannot
leave the published contract behind.

Merged into `INVENTORY_CACHE_HEADERS_SPEC`, which is what the responses
reference, so the header is documented once for every shape that carries
it.
"""

INVENTORY_CACHE_HEADERS_SPEC = {
    **PERMANENT_REDIRECT_HEADER_SPEC,
    **DATE_FETCHED_HEADER_SPEC,
    **CACHE_STATUS_HEADER_SPEC,
}
"""OpenAPI ``headers`` block for every header describing the cached copy.

All of them ride the ``200`` and the ``304`` alike, so both responses
reference this one object instead of each assembling its own set — which is
how a header comes to be documented on one shape and not the other.
"""


def _strip_weak_prefix(etag: str) -> str:
    """Strip an optional ``W/`` weakness prefix from an entity-tag."""
    if etag.startswith("W/"):
        return etag[2:]
    return etag


def _if_none_match_matches(header_value: str, current_etag: str) -> bool:
    """Return whether an ``If-None-Match`` header matches the current ETag.

    Uses RFC 9110 weak comparison: the ``W/`` weakness prefix is ignored on
    both sides and the remaining opaque-tags are compared verbatim. The
    header may carry a comma-separated list of validators, and ``*`` matches
    any current representation.
    """
    candidates = [token.strip() for token in header_value.split(",")]
    if "*" in candidates:
        return True
    normalized_current = _strip_weak_prefix(current_etag)
    return any(
        _strip_weak_prefix(candidate) == normalized_current
        for candidate in candidates
        if candidate
    )


def _permanent_redirect_headers(
    inventory: IntersphinxInventory,
) -> dict[str, str]:
    """Return the permanent-redirect header for an inventory, if warranted.

    The header is emitted only for a chain whose every hop was permanent: a
    chain with any temporary hop means the requested URL is still the right
    one to ask for (a ``latest`` alias legitimately moves), so there is
    nothing for a doc author to fix and no header. The values come from the
    stored row, so a cache hit answers without contacting the origin.

    A resolved URL past `MAX_PERMANENT_REDIRECT_URL_LENGTH` is omitted
    entirely rather than truncated: a truncated URL is worse than no
    URL, since it names a location that does not exist and cannot be
    distinguished from a real one by the client.

    The stored columns date from the row's last *successful* fetch:
    `IntersphinxInventoryStore.update_refresh_failure` retains them, so a
    row whose refreshes keep failing goes on advertising a target that may
    since have died. That is the intended trade — suppressing the header
    on a failed refresh would hide a real permanent move for a whole cache
    lifetime over one transient failure — and the endpoint's description
    tells clients to read ``Age`` to judge the observation's age.
    """
    if not (inventory.resolved_redirect_permanent and inventory.resolved_url):
        return {}
    if len(inventory.resolved_url) > MAX_PERMANENT_REDIRECT_URL_LENGTH:
        return {}
    return {PERMANENT_REDIRECT_HEADER: inventory.resolved_url}


def _date_fetched_headers(
    inventory: IntersphinxInventory,
) -> dict[str, str]:
    """Return the date-fetched header, if the row records a fetch at all.

    The value is the same ``date_fetched`` anchor the ``Age`` header counts
    from, so the two never disagree: one states the observation absolutely,
    the other relative to now.

    A row with no recorded fetch gets no header at all. This is deliberately
    unlike the ``Age`` computation, which falls back to ``0`` and so reports
    a copy of unknown age as freshly fetched; a missing observation is
    reported as missing rather than as a plausible-looking placeholder.

    The timestamp is normalized to UTC before formatting rather than
    assuming the store hands back a UTC ``tzinfo``: `safir.datetime`'s
    `isodatetime` raises on anything else, and that would be a 500 on a
    servable cache hit.
    """
    if inventory.date_fetched is None:
        return {}
    return {
        DATE_FETCHED_HEADER: isodatetime(
            inventory.date_fetched.astimezone(UTC)
        )
    }


@router.get(
    "/inventory",
    summary="Get a cached intersphinx inventory",
    description=(
        "Serve a cached Sphinx ``objects.inv`` inventory keyed by its"
        " origin URL. On a cache miss the origin is fetched"
        " synchronously, stored, and served. The response carries the"
        " stored content type and an ``Age`` header giving the seconds"
        " since the inventory was fetched from the origin. A cold-miss"
        " upstream failure returns a 502 and is negatively cached."
        "\n\n"
        "The response also carries an"
        " ``X-Ook-Inventory-Date-Fetched`` header giving that same"
        " freshness anchor as an absolute RFC 3339 UTC timestamp"
        " (``2026-08-18T17:58:24Z``) rather than as a count of seconds"
        " back from now. It is carried on a ``304`` as well as a"
        " ``200`` — ``Age`` rides the ``200`` alone — so a client that"
        " only ever revalidates still learns when its copy was last"
        " confirmed. The two headers always read the same anchor, so"
        " ``Age`` is the whole seconds elapsed since the time this"
        " header names."
        "\n\n"
        "That anchor is when Ook last **confirmed** the inventory with"
        " its origin, not when the bytes being served were downloaded:"
        " a background refresh whose conditional revalidation is"
        " answered ``304 Not Modified`` keeps the stored bytes and"
        " advances the anchor, which is the same thing ``Age`` has"
        " always reported. A cached row that records no fetch at all"
        " carries no ``X-Ook-Inventory-Date-Fetched`` header rather"
        " than a placeholder — unlike ``Age``, which falls back to"
        " ``0`` on such a row and so reports a copy of unknown age as"
        " freshly fetched."
        "\n\n"
        "Every response also carries an"
        " ``X-Ook-Inventory-Cache-Status`` header saying how Ook obtained"
        " the inventory it is describing. ``hit`` means it was served from"
        " a cached copy fetched within the freshness TTL. ``stale`` means"
        " it was served from a cached copy fetched longer ago than that"
        " TTL — still a cache serve, not an error: a copy past its TTL is"
        " deliberately retained for availability while the background"
        " refresh job revalidates it, so read ``stale`` together with"
        " ``Age`` to judge how far past it is. ``miss`` means the"
        " inventory was not cached when the request arrived, so the origin"
        " was fetched synchronously to answer it."
        "\n\n"
        "This header rides the ``304`` as well as the ``200``, where it"
        " describes how Ook obtained the copy it compared the client's"
        " validator against — not the client's own copy, which a ``304``"
        " has already said is current. A cold miss whose freshly-fetched"
        " bytes turn out to match an ``If-None-Match`` therefore reports"
        " ``miss``, not ``hit``."
        "\n\n"
        "Redirects are followed when fetching the origin. If the chain"
        " was made up entirely of permanent redirects (301 or 308), the"
        " response carries an"
        " ``X-Ook-Inventory-Permanent-Redirect`` header whose value is"
        " the URL the chain resolved to, on both a ``200`` and a"
        " ``304``. That signals the requested URL has moved for good and"
        " should be updated at its source. The header is absent when the"
        " chain included any temporary redirect — a ``latest`` alias"
        " legitimately moves, so there is nothing to fix — and when the"
        " URL did not redirect at all. Its value is read from the cached"
        " row, so it is served without re-contacting the origin, and is"
        " omitted rather than truncated when the resolved URL is"
        " implausibly long (over"
        f" {MAX_PERMANENT_REDIRECT_URL_LENGTH} characters), since a"
        " truncated URL names a location that does not exist."
        "\n\n"
        "The header reports the chain observed at the **last successful"
        " fetch** of this inventory, not the chain as it stands now. A"
        " row whose background refreshes keep failing keeps serving its"
        " last-known-good bytes and, with them, this header — the signal"
        " is deliberately not withdrawn on a failed refresh, since one"
        " transient failure would otherwise hide a real permanent move"
        " for a whole cache lifetime. Read the ``Age`` header — carried"
        " by the ``200`` — alongside it to judge how old that"
        " observation is."
        "\n\n"
        "Read this header from each response rather than from an HTTP"
        " caching layer. Withdrawal of the signal is expressed as the"
        " header's absence, and RFC 9111 §4.3.4 lets a ``304`` update"
        " the headers it carries but never delete the ones it omits: a"
        " client that stores responses in a spec-compliant cache can"
        " therefore learn the flag but will not unlearn it until the"
        " inventory bytes change and force a full ``200``."
        "\n\n"
        "This endpoint is protected by Gafaelfawr at the ingress."
    ),
    response_class=Response,
    responses={
        200: {
            "content": {"application/octet-stream": {}},
            "description": "The cached inventory bytes.",
            "headers": INVENTORY_CACHE_HEADERS_SPEC,
        },
        304: {
            "description": (
                "The client's ``If-None-Match`` validator matches the"
                " currently-cached inventory; no body is returned."
            ),
            "headers": INVENTORY_CACHE_HEADERS_SPEC,
        },
        502: {"description": "The origin inventory could not be fetched."},
    },
)
async def get_intersphinx_inventory(
    *,
    url: Annotated[
        str,
        Query(
            title="Inventory URL",
            description="The full origin ``objects.inv`` URL to serve.",
            examples=["https://www.sphinx-doc.org/en/master/objects.inv"],
        ),
    ],
    if_none_match: Annotated[
        str | None,
        Header(
            description=(
                "A conditional-request validator. When it matches the"
                " currently-cached inventory's ``ETag`` (RFC 9110 weak"
                " comparison; ``*`` matches any cached representation), the"
                " endpoint responds ``304 Not Modified`` with no body."
            ),
        ),
    ] = None,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> Response:
    """Serve a cached intersphinx inventory, fetching on a cache miss."""
    service = context.factory.create_intersphinx_cache_service()
    try:
        served = await service.get_inventory(url)
    except UpstreamInventoryError:
        # The service wrote a negative-cache row before raising; commit it
        # so the failure is actually cached even though the client gets a
        # 502. The handler-managed transaction would otherwise roll it back.
        await context.session.commit()
        raise
    await context.session.commit()
    inventory = served.inventory

    age = 0
    if inventory.date_fetched is not None:
        age = max(
            0,
            int(
                (datetime.now(tz=UTC) - inventory.date_fetched).total_seconds()
            ),
        )
    # A strong ETag identifying the bytes Ook currently serves: the quoted
    # SHA-256 hex digest of the served content (RFC 9110). It is hashed per
    # request over the 100-500 KB body; a stored digest is a later
    # optimization. This is distinct from ``inventory.etag``, which is the
    # origin's upstream validator.
    content = inventory.content or b""
    etag = f'"{hashlib.sha256(content).hexdigest()}"'

    # Facts about the cached row itself and about this serve of it, carried
    # on both response shapes: a permanently-moved inventory URL, when that
    # row was last confirmed with its origin, and how this request obtained
    # it. A client that holds the current bytes only ever revalidates, so
    # anything reported on the 200 alone would reach it exactly once.
    # ``Age`` is the deliberate exception — it is the 200's own freshness
    # statement about a body, and a bodyless 304 has none.
    #
    # The cache status is the one value here that is not read from the row:
    # it comes from the service, which decided it while serving, because the
    # row cannot be asked afterwards which serve it was part of.
    cache_headers = {
        CACHE_STATUS_HEADER: served.cache_status.value,
        **_permanent_redirect_headers(inventory),
        **_date_fetched_headers(inventory),
    }

    # Conditional-request handling: when the client already holds the
    # currently-cached representation, revalidate cheaply with a bodyless 304
    # that carries only the ETag (not the Age-bearing 200 response shape).
    if if_none_match is not None and _if_none_match_matches(
        if_none_match, etag
    ):
        return Response(
            status_code=304, headers={"ETag": etag, **cache_headers}
        )

    return Response(
        content=inventory.content,
        media_type=inventory.content_type or "application/octet-stream",
        headers={"Age": str(age), "ETag": etag, **cache_headers},
    )
