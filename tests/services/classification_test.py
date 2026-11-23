"""Tests for the ClassificationService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from safir.datetime import isodatetime
from structlog.testing import capture_logs

from ook.domain.algoliarecord import DocumentSourceType
from ook.exceptions import LtdProjectNotFoundError
from ook.factory import Factory


@pytest.mark.asyncio
async def test_technote_classification(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """Test classifying a technote (LTD_TECHNOTE)."""
    data_root = Path(__file__).parent.parent / "data" / "content" / "sqr-075"
    html = (data_root / "html" / "index.html").read_text()

    respx_mock.get("https://sqr-075.lsst.io/").respond(
        status_code=200, html=html
    )
    classifier = factory.create_classification_service()
    print("calling classifier")
    assert classifier._http_client.is_closed is False
    result = await classifier.classify_ltd_site(
        product_slug="sqr-075", published_url="https://sqr-075.lsst.io/"
    )
    assert result == DocumentSourceType.LTD_TECHNOTE


KEEPER = "https://keeper.lsst.codes"


def _mock_ltd_project(
    respx_mock: respx.Router, slug: str, *, site_status: int, html: str = ""
) -> None:
    """Mock the LTD API resources and published site for a project."""
    published_url = f"https://{slug}.lsst.io"
    respx_mock.get(f"{KEEPER}/products/{slug}").respond(
        json={
            "slug": slug,
            "published_url": published_url,
            "self_url": f"{KEEPER}/products/{slug}",
            "doc_repo": f"https://github.com/lsst-sqre/{slug}",
        }
    )
    respx_mock.get(f"{KEEPER}/products/{slug}/editions/").respond(
        json={"editions": [f"{KEEPER}/editions/{slug}-main"]}
    )
    respx_mock.get(f"{KEEPER}/editions/{slug}-main").respond(
        json={
            "slug": "main",
            "published_url": published_url,
            "self_url": f"{KEEPER}/editions/{slug}-main",
            "build_url": f"{KEEPER}/builds/{slug}-1",
            "date_rebuilt": isodatetime(datetime.now(tz=UTC)),
        }
    )
    respx_mock.get(published_url).respond(status_code=site_status, html=html)


@pytest.mark.asyncio
async def test_updated_projects_skips_not_found(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """A project whose site or API resource 404s is skipped with a warning
    rather than failing the whole ``ingest-updated`` run.
    """
    data_root = Path(__file__).parent.parent / "data" / "content" / "sqr-075"
    html = (data_root / "html" / "index.html").read_text()

    respx_mock.get(f"{KEEPER}/products/").respond(
        json={
            "products": [
                f"{KEEPER}/products/testn-166",
                f"{KEEPER}/products/testn-167",
                f"{KEEPER}/products/sqr-075",
            ]
        }
    )
    # Registered in LTD, but the published site is a 404.
    _mock_ltd_project(respx_mock, "testn-166", site_status=404)
    # Listed by LTD, but the project resource itself is a 404.
    respx_mock.get(f"{KEEPER}/products/testn-167").respond(status_code=404)
    _mock_ltd_project(respx_mock, "sqr-075", site_status=200, html=html)

    classifier = factory.create_classification_service()
    publisher = AsyncMock()
    classifier._kafka_ingest_publisher = publisher

    with capture_logs() as logs:
        await classifier.queue_ingest_for_updated_ltd_projects(
            timedelta(days=2)
        )

    publisher.publish.assert_awaited_once()
    message = publisher.publish.await_args.kwargs["message"]
    assert message["project"]["slug"] == "sqr-075"

    warnings = {
        log["slug"]: log["url"]
        for log in logs
        if log["log_level"] == "warning"
    }
    assert warnings == {
        "testn-166": "https://testn-166.lsst.io",
        "testn-167": f"{KEEPER}/products/testn-167",
    }
    summary = logs[-1]
    assert summary["count"] == 1
    assert summary["not_found"] == 1


@pytest.mark.asyncio
async def test_queue_ingest_not_found_raises(
    factory: Factory,
    respx_mock: respx.Router,
) -> None:
    """Queueing a single project surfaces a 404 as a typed error; other
    error statuses still raise.
    """
    _mock_ltd_project(respx_mock, "testn-166", site_status=404)
    _mock_ltd_project(respx_mock, "testn-168", site_status=500)

    classifier = factory.create_classification_service()
    classifier._kafka_ingest_publisher = AsyncMock()

    with pytest.raises(LtdProjectNotFoundError):
        await classifier.queue_ingest_for_ltd_product_slug(
            product_slug="testn-166", edition_slug="main"
        )
    assert not await classifier.queue_ingest_for_ltd_product_slug_if_found(
        product_slug="testn-166", edition_slug="main"
    )
    with pytest.raises(httpx.HTTPStatusError):
        await classifier.queue_ingest_for_ltd_product_slug_if_found(
            product_slug="testn-168", edition_slug="main"
        )
