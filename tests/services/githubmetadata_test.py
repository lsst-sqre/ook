"""Tests for the GitHub metadata service."""

from __future__ import annotations

import pytest
import structlog
from httpx import AsyncClient
from safir.github import GitHubAppClientFactory

from ook.services.githubmetadata import GitHubMetadataService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "expected_owner", "expected_name"),
    [
        ("https://github.com/lsst-sqre/sqr-020", "lsst-sqre", "sqr-020"),
        ("https://github.com/lsst-sqre/sqr-020.git", "lsst-sqre", "sqr-020"),
        (
            "https://github.com/lsst-sqre/sqr-020/settings",
            "lsst-sqre",
            "sqr-020",
        ),
    ],
)
async def test_parse_repo_url(
    url: str,
    expected_owner: str,
    expected_name: str,
    http_client: AsyncClient,
) -> None:
    """Test parsing a GitHub repository URL into an owner and name."""
    logger = structlog.get_logger()
    gh_factory = GitHubAppClientFactory(
        id=12345,
        key="secret",
        name="lsst-sqre/ook",
        http_client=http_client,
    )
    service = GitHubMetadataService(gh_factory=gh_factory, logger=logger)
    owner, name = service.parse_repo_from_github_url(url)
    assert owner == expected_owner
    assert name == expected_name


@pytest.mark.asyncio
async def test_format_raw_url(http_client: AsyncClient) -> None:
    """Test formatting a raw GitHub URL."""
    expected = (
        "https://raw.githubusercontent.com/lsst-sqre/ook/main/pyproject.toml"
    )
    logger = structlog.get_logger()
    gh_factory = GitHubAppClientFactory(
        id=12345,
        key="secret",
        name="lsst-sqre/ook",
        http_client=http_client,
    )
    service = GitHubMetadataService(gh_factory=gh_factory, logger=logger)
    result = service.format_raw_content_url(
        owner="lsst-sqre",
        repo="ook",
        path="pyproject.toml",
        git_ref="main",
    )
    assert result == expected
