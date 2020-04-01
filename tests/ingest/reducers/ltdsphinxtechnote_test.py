"""Tests for the ook.reducers.ltdsphinxtechnote module."""

from __future__ import annotations

import datetime
from pathlib import Path

import yaml

from ook.ingest.reducers.ltdsphinxtechnote import ReducedLtdSphinxTechnote


def test_sqr035_reduction() -> None:
    """Test ReducedLtdSphinxTechnote using SQR-035 as a test dataset."""
    sqr035data = (
        Path(__file__).parent.parent.parent / "data" / "content" / "sqr-035"
    )
    html_source = (sqr035data / "index.html").read_text()
    metadata = yaml.safe_load((sqr035data / "metadata.yaml").read_text())
    url = "https://sqr-035.lsst.io/"

    reduced_technote = ReducedLtdSphinxTechnote(
        html_source=html_source, url=url, metadata=metadata
    )

    assert reduced_technote.url == "https://sqr-035.lsst.io/"
    assert reduced_technote.h1 == (
        "Deployment engineering for Kubernetes-based services."
    )
    assert reduced_technote.timestamp == datetime.datetime(2019, 10, 29, 0, 0)
    assert reduced_technote.github_url == (
        "https://github.com/lsst-sqre/sqr-035"
    )

    assert reduced_technote.description == (
        "Configuration management and deployment infrastructure for "
        "Kubernetes-based services for the LSST Science Platform and "
        "SQuaRE Services. "
    )

    assert reduced_technote.series == "SQR"
    assert reduced_technote.number == "035"
    assert reduced_technote.handle == "SQR-035"
    assert reduced_technote.author_names == [
        "Frossie Economou",
        "Jonathan Sick",
        "Christine Banek",
        "Adam Thornton",
        "Josh Hoblitt",
        "Angelo Fausti",
        "Simon Krughoff",
    ]

    sections = reduced_technote.sections
    assert sections[0].url == "https://sqr-035.lsst.io/#context"
    assert sections[0].headers == [reduced_technote.h1, "1   Context"]
    assert sections[0].content.startswith(
        "This document does three things: lays out the elements and practice "
        "we use for kubernetes-based services, outlines guidelines for best "
        "practices, and a discussion of current and upcoming technological "
        "choices for implementation."
    )

    assert sections[1].url == "https://sqr-035.lsst.io/#docker-image-release"
    assert sections[1].headers == [
        reduced_technote.h1,
        "2   Elements",
        "2.1   Docker Image Release",
    ]
    assert (
        sections[2].url == "https://sqr-035.lsst.io/#configuration-management"
    )
    assert sections[2].headers == [
        reduced_technote.h1,
        "2   Elements",
        "2.2   Configuration Management",
    ]

    assert sections[3].url == "https://sqr-035.lsst.io/#secrets"
    assert sections[3].headers == [
        reduced_technote.h1,
        "2   Elements",
        "2.3   Secrets",
    ]

    assert (
        sections[4].url == "https://sqr-035.lsst.io/#deployment-orchestration"
    )
    assert sections[4].headers == [
        reduced_technote.h1,
        "2   Elements",
        "2.4   Deployment Orchestration",
    ]

    assert sections[5].url == "https://sqr-035.lsst.io/#configuration-control"
    assert sections[5].headers == [
        reduced_technote.h1,
        "2   Elements",
        "2.5   Configuration Control",
    ]

    assert sections[6].url == "https://sqr-035.lsst.io/#elements"
    assert sections[6].headers == [reduced_technote.h1, "2   Elements"]

    assert sections[7].url == "https://sqr-035.lsst.io/#deployment-add-ons"
    assert sections[7].headers == [
        reduced_technote.h1,
        "3   Deployment add-ons",
    ]
