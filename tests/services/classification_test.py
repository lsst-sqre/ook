"""Tests for the ClassificationService."""

from __future__ import annotations

from pathlib import Path

import pytest
import respx

from ook.domain.algoliarecord import DocumentSourceType
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
