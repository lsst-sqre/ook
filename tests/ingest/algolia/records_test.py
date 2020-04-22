"""Tests for the ook.ingest.algolia.records module."""

from __future__ import annotations

from pathlib import Path

import yaml

from ook.ingest.algolia.records import LtdSphinxTechnoteSectionRecord
from ook.ingest.reducers.ltdsphinxtechnote import ReducedLtdSphinxTechnote


def test_sqr035_record() -> None:
    """Test the LtdSphinxTechnoteSectionRecord for the SQR-035 dataset."""
    sqr035data = (
        Path(__file__).parent.parent.parent / "data" / "content" / "sqr-035"
    )
    html_source = (sqr035data / "index.html").read_text()
    metadata = yaml.safe_load((sqr035data / "metadata.yaml").read_text())
    url = "https://sqr-035.lsst.io/"

    reduced_technote = ReducedLtdSphinxTechnote(
        html_source=html_source, url=url, metadata=metadata
    )
    section = reduced_technote.sections[0]

    record = LtdSphinxTechnoteSectionRecord(
        section=section, technote=reduced_technote, surrogate_key="test-key"
    )
    data = record.data
    assert data["url"] == "https://sqr-035.lsst.io/#context"
    assert data["baseUrl"] == "https://sqr-035.lsst.io/"
    assert data["content"].startswith(
        "This document does three things: lays out the elements and practice "
        "we use for kubernetes-based services, outlines guidelines for best "
        "practices, and a discussion of current and upcoming technological "
        "choices for implementation."
    )
    assert data["importance"] == 2
    assert data["contentType"] == "document"
    assert data["series"] == "SQR"
    assert data["number"] == "035"
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
