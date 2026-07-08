"""Endpoints for the /ook/linkcheck APIs."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import AfterValidator
from safir.models import ErrorModel

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency
from ook.domain.base32id import Base32Id, serialize_ook_base32_id
from ook.domain.kafka import CheckLinksMessageV1
from ook.domain.linkcheck import (
    CheckRunStatus,
    CheckUrlStatus,
    normalize_origin_base_url,
)
from ook.exceptions import NotFoundError
from ook.storage.linkcheckstore import OriginLinksCursor

from .models import LinkCheck, LinkCheckRequest, OriginLink, UrlRecord

router = APIRouter(
    prefix=f"{config.path_prefix}/linkcheck", tags=["linkcheck"]
)
"""FastAPI router for all linkcheck handlers."""


@router.post(
    "/checks",
    summary="Submit a link check",
    description=(
        "Submit a website build's external URLs for checking. URLs are"
        " canonicalized (fragments stripped) and partitioned: URLs with"
        " a fresh cached result and unsupported URLs resolve"
        " immediately, while the rest are checked asynchronously. The"
        " created check resource is returned as the body and its URL as"
        " the Location header. A check whose URLs all resolve"
        " immediately is returned complete with status 200; otherwise"
        " the response is 202 and the check should be polled at the"
        " Location header (or the body's self_url) until complete. This"
        " endpoint is write-protected by Gafaelfawr at the ingress."
    ),
    status_code=202,
    responses={
        200: {
            "description": (
                "The check completed at submission (all URLs resolved"
                " immediately); the body holds the full results."
            ),
            "model": LinkCheck,
        },
        202: {
            "description": (
                "The check was accepted and is executing"
                " asynchronously; poll it at the Location header."
            ),
            "model": LinkCheck,
        },
        422: {"description": "Invalid submission"},
    },
)
async def post_linkcheck_check(
    check_request: LinkCheckRequest,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> LinkCheck:
    """Accept a link-check submission and return the check resource."""
    context.logger.info(
        "Received link-check submission",
        origin_base_url=check_request.origin_base_url,
        is_default_version=check_request.is_default_version,
        url_count=len(check_request.urls),
    )
    async with context.session.begin():
        service = context.factory.create_linkcheck_service()
        submission = await service.submit_check(
            origin_base_url=check_request.origin_base_url,
            is_default_version=check_request.is_default_version,
            urls=[url.to_domain() for url in check_request.urls],
        )
        report = await service.get_check_report(submission.check_id)
        if report is None:
            raise RuntimeError(
                f"Link check {submission.check_id} not found after creation"
            )
    if submission.due_urls:
        # Enqueue execution only after the transaction commits so the
        # consumer never sees a check id before its row is visible.
        message = CheckLinksMessageV1(check_id=submission.check_id)
        await context.factory.kafka_linkcheck_publisher.publish(
            message.model_dump(mode="json")
        )
    location = str(
        context.request.url_for(
            "get_linkcheck_check",
            check_id=serialize_ook_base32_id(submission.check_id),
        )
    )
    context.response.headers["Location"] = location
    if report.status is CheckRunStatus.complete:
        # The all-fresh fast path completes at submission, so this is a
        # final resource representation, not an accepted async job.
        context.response.status_code = 200
    return LinkCheck.from_domain(report, request=context.request)


@router.get(
    "/checks/{check_id}",
    summary="Get link check status",
    description=(
        "Poll a submitted link check: its processing status"
        " (pending/in_progress/complete), summary counts by URL status,"
        " and per-URL results."
    ),
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def get_linkcheck_check(
    *,
    check_id: Annotated[
        Base32Id,
        Path(
            title="Check ID",
            description="The Crockford Base32 identifier of the link check.",
            examples=["1234-5678-90ab-cd2f"],
        ),
    ],
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> LinkCheck:
    """Get a link check's status and per-URL results."""
    async with context.session.begin():
        service = context.factory.create_linkcheck_service()
        report = await service.get_check_report(check_id)
        if report is None:
            raise NotFoundError(message=f"Link check {check_id} not found")
        return LinkCheck.from_domain(report, request=context.request)


@router.get(
    "/urls",
    summary="Get a URL's health record",
    description=(
        "Look up the stored health record of a single canonical URL:"
        " its status, HTTP status code, redirect location, check"
        " timestamps, and the origin pages it occurs on. The lookup"
        " URL is canonicalized (fragment stripped) first."
    ),
    responses={404: {"description": "Not found", "model": ErrorModel}},
)
async def get_linkcheck_url(
    *,
    url: Annotated[
        str,
        Query(
            title="URL",
            description=(
                "The URL to look up. Fragments are stripped before the lookup."
            ),
            examples=["https://www.lsst.io/"],
        ),
    ],
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> UrlRecord:
    """Get the stored health record of a single canonical URL."""
    async with context.session.begin():
        service = context.factory.create_linkcheck_service()
        record = await service.get_url_record(url)
        if record is None:
            raise NotFoundError(message=f"URL {url} not found")
        return UrlRecord.from_domain(record)


@router.get(
    "/links",
    summary="List an origin's links",
    description=(
        "List the links recorded for an origin website with their"
        " health states and the page paths where they occur, ordered"
        " by URL. Results are paginated with a cursor (`Link` header"
        " with next/prev URLs and an `X-Total-Count` header). Filter by"
        " status: `?status=redirected` lists links whose sources should"
        " be updated to their new locations; `?status=broken` is the"
        " rot-monitoring view. Filter by page with `?path=<page>` to"
        " list only the links occurring on that page; each listed link"
        " still reports its full set of page paths. `?status=` and"
        " `?path=` compose."
    ),
)
async def get_origin_links(
    *,
    origin: Annotated[
        str,
        AfterValidator(normalize_origin_base_url),
        Query(
            title="Origin base URL",
            description=(
                "The base URL of the origin website whose links are"
                " listed. It is normalized (lowercased host, trailing"
                " slash stripped) before the lookup."
            ),
            examples=["https://sqr-000.lsst.io"],
        ),
    ],
    status: Annotated[
        CheckUrlStatus | None,
        Query(
            title="Status filter",
            description=(
                "Only list links with this status. Use ``redirected``"
                " for links whose sources should be updated to their"
                " new locations and ``broken`` for the rot-monitoring"
                " view."
            ),
        ),
    ] = None,
    path: Annotated[
        str | None,
        Query(
            title="Page path filter",
            description=(
                "Only list links that occur on this origin page path."
                " Each listed link still reports its full set of page"
                " paths; this only narrows which links are listed."
            ),
            examples=["guide/installation"],
        ),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(
            title="Pagination cursor",
            description="Cursor to navigate paginated results.",
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            title="Row limit",
            description="Maximum number of entries to return.",
            examples=[100],
            ge=1,
            le=100,
        ),
    ] = 100,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> list[OriginLink]:
    """List an origin's links with their health states."""
    async with context.session.begin():
        service = context.factory.create_linkcheck_service()
        results = await service.get_origin_links(
            origin,
            status=status,
            path=path,
            cursor=(
                OriginLinksCursor.from_str(cursor)
                if cursor is not None
                else None
            ),
            limit=limit,
        )
        response = context.response
        response.headers["Link"] = results.link_header(context.request.url)
        response.headers["X-Total-Count"] = str(results.count)
        return [OriginLink.from_domain(link) for link in results.entries]
