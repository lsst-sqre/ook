"""Tests for ook.ingest.workflows.ltdsphinxtechnote."""

from __future__ import annotations

from pathlib import Path

import structlog
import yaml

from ook.ingest.reducers.ltdsphinxtechnote import ReducedLtdSphinxTechnote
from ook.ingest.workflows.ltdsphinxtechnote import create_record


def test_sqr035_record() -> None:
    """Test creating an Algolia document record with the SQR-035 dataset."""
    logger = structlog.get_logger("ook")

    sqr035data = (
        Path(__file__).parent.parent.parent / "data" / "content" / "sqr-035"
    )
    html_source = (sqr035data / "index.html").read_text()
    metadata = yaml.safe_load((sqr035data / "metadata.yaml").read_text())
    url = "https://sqr-035.lsst.io/"

    reduced_technote = ReducedLtdSphinxTechnote(
        html_source=html_source, url=url, metadata=metadata, logger=logger
    )
    section = reduced_technote.sections[1]

    data = create_record(
        section=section,
        technote=reduced_technote,
        surrogate_key="test-key",
        creation_date=None,
    )

    assert data["url"] == "https://sqr-035.lsst.io/#context"
    assert data["baseUrl"] == "https://sqr-035.lsst.io/"
    assert data["content"].startswith(
        "This document does three things: lays out the elements and practice "
        "we use for kubernetes-based services, outlines guidelines for best "
        "practices, and a discussion of current and upcoming technological "
        "choices for implementation."
    )
    assert data["importance"] == 2
    assert data["series"] == "SQR"
    assert data["number"] == 35
    assert data["handle"] == "SQR-035"
    assert data["authorNames"] == [
        "Frossie Economou",
        "Jonathan Sick",
        "Christine Banek",
        "Adam Thornton",
        "Josh Hoblitt",
        "Angelo Fausti",
        "Simon Krughoff",
    ]
    assert data["objectID"] == (
        "aHR0cHM6Ly9zcXItMDM1Lmxzc3QuaW8vI2NvbnRleHQ=-RGVwbG95bWVudCBlbmdpbmV"
        "lcmluZyBmb3IgS3ViZXJuZXRlcy1iYXNlZCBzZXJ2aWNlcy4gMSAgIENvbnRleHQ="
    )
    assert data["surrogateKey"] == "test-key"
    assert data["sourceUpdateTime"] == "2019-10-29T00:00:00Z"
    assert isinstance(data["recordUpdateTime"], str)
    assert data["githubRepoUrl"] == "https://github.com/lsst-sqre/sqr-035"
    assert data["description"] == (
        "Configuration management and deployment infrastructure for "
        "Kubernetes-based services for the LSST Science Platform and SQuaRE "
        "Services. "
    )
    assert data["contentCategories.lvl0"] == "Documents"
    assert data["contentCategories.lvl1"] == "Documents > SQR"
    assert data["contentType"] == "ltd_sphinx_technote"
