"""Tests for the ook.ingest.reducers.ltdlander module."""

from __future__ import annotations

import json
from pathlib import Path

from structlog import get_logger

from ook.ingest.reducers.ltdlander import ReducedLtdLanderDocument


def test_dmtn131_reduction() -> None:
    """Test ReducedLtdLanderDocument using DMTN-131 as a test dataset."""
    logger = get_logger("ook")
    data_root = (
        Path(__file__).parent.parent.parent / "data" / "content" / "dmtn-131"
    )
    metadata = json.loads((data_root / "metadata.json").read_text())
    url = "https://dmtn-131.lsst.io/"

    doc = ReducedLtdLanderDocument(url=url, metadata=metadata, logger=logger)

    assert doc.h1 == "When clouds might be good for LSST"
    assert doc.handle == "DMTN-131"
    assert doc.series == "DMTN"
    assert doc.number == 131
    assert doc.url == url
    assert doc.author_names == ["William O’Mullane"]
    assert doc.github_url == "https://github.com/lsst-dm/dmtn-131"
    assert doc.description == (
        "In this short note we would like to consider potential annual "
        "operating costs for LSST as well as discuss long term archiving. The "
        "goal would be to see if we can come to an agreement with a major "
        "cloud provider."
    )
    assert doc.chunks[0].headers == [
        "When clouds might be good for LSST",
        "INTRODUCTION",
    ]
    assert doc.chunks[0].paragraph == 0
    assert doc.chunks[0].content.startswith(
        "The Large Synoptic Survey Telescope"
    )

    assert doc.chunks[1].headers == [
        "When clouds might be good for LSST",
        "INTRODUCTION",
    ]
    assert doc.chunks[1].paragraph == 1
