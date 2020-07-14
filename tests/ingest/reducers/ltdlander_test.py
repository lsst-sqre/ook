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


def test_dmtn153_reduction() -> None:
    """test ReducedLtdLanderDocument using DMTN-153 as a test document since
    it has an abstract, but no content.
    """
    logger = get_logger("ook")
    data_root = (
        Path(__file__).parent.parent.parent / "data" / "content" / "dmtn-153"
    )
    metadata = json.loads((data_root / "metadata.json").read_text())
    url = "https://dmtn-153.lsst.io/"

    doc = ReducedLtdLanderDocument(url=url, metadata=metadata, logger=logger)
    assert len(doc.chunks) == 1


def test_dmtn096_reduction() -> None:
    """Test DMTN-096, which is an example of a technote that doens't have an
    abstract set.
    """
    logger = get_logger("ook")
    data_root = (
        Path(__file__).parent.parent.parent / "data" / "content" / "dmtn-096"
    )
    metadata = json.loads((data_root / "metadata.json").read_text())
    url = "https://dmtn-096.lsst.io/"

    doc = ReducedLtdLanderDocument(url=url, metadata=metadata, logger=logger)
    assert doc.description == (
        "DM has been asked how we can save 10% of its remaining cost (≈ $7M). "
        "The official scope options which one might invoke are listed in , "
        "but we have also identified some further possibilities which are "
        "described in this document. This note also discusses the practical "
        "aspects of invoking these options."
    )


def test_dmtn145_reduction() -> None:
    """Test DMTN-145, which is a AASTeX-formatted technote that doesn't have
    full metadata extraction support from Lander.
    """
    logger = get_logger("ook")
    data_root = (
        Path(__file__).parent.parent.parent / "data" / "content" / "dmtn-145"
    )
    metadata = json.loads((data_root / "metadata.json").read_text())
    url = "https://dmtn-145.lsst.io/"

    doc = ReducedLtdLanderDocument(url=url, metadata=metadata, logger=logger)
    assert doc.handle == "DMTN-145"
    assert doc.series == "DMTN"
    assert doc.number == 145
    assert doc.description == (
        "In the last two years, the software landscape on LSST has changed "
        "dramatically. We are transitioning from siloed software teams to a "
        "more integrated approach. LSST has Labview, C++, Python and Java "
        "components - these are built in different ways and do not employ "
        "the same test harnesses. We are attempting to align all "
        "build/testing on Jenkins, although this is challenging for LabView "
        "for example. In Data Management all testing is done via pytest, all "
        "C++ code is exposed to Python so we may test it all in the python "
        "layer. Since we have a common access layer to all Telescope control "
        "components (OpenSplice) we could follow the same approach there. "
        "Next we are approaching deployment on the summit. To date "
        "deployments in telescope control have been fairly manual. As we "
        "shifted more toward Python, containers became more prevalent. Now "
        "most components can be deployed with Docker and Docker compose. The "
        "next logical step for them is to move to Kubernetes; this may not "
        "be possible for the camera control system but we will try to pursue "
        "this as much as possible. Data Management is already deploying the "
        "Science Platform using Kubernetes. Though the processing software "
        "for data releases is containerised it is not yet utilized in that "
        "manner. Finally the bare metal provisioning is not fully automated "
        "- we have successfully experimented with Foreman and Puppet to bring "
        "up new blades in a selected manner. Our approach here is to "
        "provision to Kubernetes as much as possible but for other specific "
        "machines, such as camera control, to at least provision the machine "
        "with Puppet to the level needed by the camera control system. There "
        "are strong management/cultural issues in bringing these efforts "
        "together. These are prevalent in all large projects and some of "
        "these issues will be touched on in the talk also."
    )


def test_dmtn119_reduction() -> None:
    """Test DMTN-119, which doesn't have ideal content conversion from
    Pandoc.
    """
    logger = get_logger("ook")
    data_root = (
        Path(__file__).parent.parent.parent / "data" / "content" / "dmtn-119"
    )
    metadata = json.loads((data_root / "metadata.json").read_text())
    url = "https://dmtn-119.lsst.io/"

    doc = ReducedLtdLanderDocument(url=url, metadata=metadata, logger=logger)
    assert doc.handle == "DMTN-119"
    # After all the heuristic rejections there should only be the
    # description's chunk left
    assert len(doc.chunks) == 1
