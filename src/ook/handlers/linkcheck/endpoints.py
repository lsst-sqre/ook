"""Endpoints for the /ook/linkcheck APIs."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response
from safir.models import ErrorModel

from ook.config import config
from ook.dependencies.context import RequestContext, context_dependency
from ook.domain.kafka import CheckLinksMessageV1
from ook.domain.linkcheck import CheckUrlStatus
from ook.exceptions import NotFoundError
from ook.storage.linkcheckstore import ProjectLinksCursor

from .models import LinkCheck, LinkCheckRequest, ProjectLink, UrlRecord

router = APIRouter(
    prefix=f"{config.path_prefix}/linkcheck", tags=["linkcheck"]
)
"""FastAPI router for all linkcheck handlers."""


@router.post(
    "/checks",
    summary="Submit a link check",
    description=(
        "Submit a documentation build's external URLs for checking."
        " URLs are canonicalized (fragments stripped) and partitioned:"
        " URLs with a fresh cached result and unsupported URLs resolve"
        " immediately, while the rest are checked asynchronously. Poll"
        " the check at the returned Location header. This endpoint is"
        " write-protected by Gafaelfawr at the ingress."
    ),
    status_code=202,
    response_model=None,
    responses={422: {"description": "Invalid submission"}},
)
async def post_linkcheck_check(
    check_request: LinkCheckRequest,
    context: Annotated[RequestContext, Depends(context_dependency)],
) -> Response:
    """Accept a link-check submission and return its polling location."""
    context.logger.info(
        "Received link-check submission",
        ltd_slug=check_request.ltd_slug,
        default_branch=check_request.default_branch,
        url_count=len(check_request.urls),
    )
    async with context.session.begin():
        service = context.factory.create_linkcheck_service()
        submission = await service.submit_check(
            ltd_slug=check_request.ltd_slug,
            default_branch=check_request.default_branch,
            urls=[url.to_domain() for url in check_request.urls],
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
            "get_linkcheck_check", check_id=submission.check_id
        )
    )
    return Response(status_code=202, headers={"Location": location})


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
        int,
        Path(
            title="Check ID",
            description="Identifier of the link check.",
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
        " timestamps, and the project pages it occurs on. The lookup"
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
    "/projects/{slug}/links",
    summary="List a project's links",
    description=(
        "List the links recorded for an LSST the Docs project's"
        " documentation with their health states and the page paths"
        " where they occur, ordered by URL. Results are paginated with"
        " a cursor (`Link` header with next/prev URLs and an"
        " `X-Total-Count` header). Filter by status:"
        " `?status=redirected` lists links whose sources should be"
        " updated to their new locations; `?status=broken` is the"
        " rot-monitoring view."
    ),
)
async def get_project_links(
    *,
    slug: Annotated[
        str,
        Path(
            title="Project slug",
            description="The LSST the Docs project slug.",
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
) -> list[ProjectLink]:
    """List a project's links with their health states."""
    async with context.session.begin():
        service = context.factory.create_linkcheck_service()
        results = await service.get_project_links(
            slug,
            status=status,
            cursor=(
                ProjectLinksCursor.from_str(cursor)
                if cursor is not None
                else None
            ),
            limit=limit,
        )
        response = context.response
        response.headers["Link"] = results.link_header(context.request.url)
        response.headers["X-Total-Count"] = str(results.count)
        return [ProjectLink.from_domain(link) for link in results.entries]
