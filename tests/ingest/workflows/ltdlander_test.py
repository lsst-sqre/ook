"""Test for ook.ingest.workflows.lander document ingest."""

from __future__ import annotations

import json
from pathlib import Path

from ook.ingest.reducers.ltdlander import ReducedLtdLanderDocument
from ook.ingest.workflows.ltdlander import create_record


def test_dmtn131_ingest() -> None:
    data_root = (
        Path(__file__).parent.parent.parent / "data" / "content" / "dmtn-131"
    )
    metadata = json.loads((data_root / "metadata.json").read_text())
    url = "https://dmtn-131.lsst.io/"

    doc = ReducedLtdLanderDocument(url=url, metadata=metadata)
    chunk = doc.chunks[0]

    record = create_record(document=doc, chunk=chunk, surrogate_key="test-key")

    assert record["url"] == url
    assert record["baseUrl"] == url
    assert record["content"].startswith("The Large Synoptic Survey Telescope")
    assert record["h1"] == "When clouds might be good for LSST"
    assert record["h2"] == "INTRODUCTION"
    assert record["pIndex"] == 0
    assert record["handle"] == "DMTN-131"
    assert record["series"] == "DMTN"
    assert record["number"] == "131"
    assert record["authorNames"] == ["William O’Mullane"]
    assert record["githubRepoUrl"] == "https://github.com/lsst-dm/dmtn-131"
    assert record["description"] == (
        "In this short note we would like to consider potential annual "
        "operating costs for LSST as well as discuss long term archiving. The "
        "goal would be to see if we can come to an agreement with a major "
        "cloud provider."
    )
    assert record["surrogateKey"] == "test-key"
    assert record["contentType"] == "ltd_lander_jsonld"
    assert record["contentCategories.lvl0"] == "Documents"
    assert record["contentCategories.lvl1"] == "Documents > DMTN"
