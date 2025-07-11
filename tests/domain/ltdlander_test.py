"""Test for ook.domain.ltdlander."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import structlog

from ook.domain.ltdlander import ReducedLtdLanderDocument


@pytest.fixture
def sitcomtn_154_metadata() -> dict[str, Any]:
    path = (
        Path(__file__).parent.parent
        / "data"
        / "content"
        / "sitcomtn-154.metadata.jsonld"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_sitcomtn_154(sitcomtn_154_metadata: dict[str, Any]) -> None:
    """Test parsing SitComTN-154 into a LtdLander domain object."""
    doc = ReducedLtdLanderDocument(
        url="https://sitcomtn-154.lsst.io/",
        metadata=sitcomtn_154_metadata,
        logger=structlog.stdlib.get_logger(),
    )

    assert doc.url == "https://sitcomtn-154.lsst.io/"
    assert (
        doc.h1
        == "Initial studies of photometric redshifts with LSSTComCam from DP1"
    )
    assert doc.description == (
        "This technote holds reports based on the first analyses of the "
        "Data Preview 1 (DP1) data by the Science Unit for photometric "
        "redshifts. Although photometric redshifts are not a official DP1 "
        'data product, the "Photo-z Science Unit" generated photo-z '
        "estimates for every galaxy in DP1 using the available multi-band "
        "imaging on a best-effort basis. This work included developing "
        "training and test datasets by matching DP1 data to high-quality "
        "reference redshifts obtained with spectroscopy, Grism data, and "
        "multi-band photometry. The Science Unit used the RAIL software "
        "package to make photometric redshift estimates using eight "
        "different algorithms, developed simple scientific performance "
        "metrics, used those metrics to explore how the performance of the "
        "algorithms varied with configuration changes, derived more "
        "optimized configurations of the algorithms and tested the "
        "performance of those configurations. This work, the resulting data "
        "products and expected data distribution mechanism are all described "
        "there."
    )
    assert doc.series == "SITCOMTN"
    assert doc.handle == "SITCOMTN-154"
