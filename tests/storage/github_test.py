"""Tests for ook.storage.github."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import httpx
import pytest
import respx
import structlog
import yaml
from gidgethub.httpx import GitHubAPI

from ook.storage.github import GitHubRepoStore


@pytest.mark.asyncio
@pytest.mark.respx(base_url="https://api.github.com")
async def test_recursive_git_tree(
    respx_mock: respx.MockRouter, http_client: httpx.AsyncClient
) -> None:
    data_root = (
        Path(__file__).parent.parent / "data" / "github" / "sdm_schemas"
    )
    respx_mock.get(
        "/repos/lsst/sdm_schemas/git/trees/w.2025.10?recursive=1"
    ).mock(
        return_value=httpx.Response(
            200,
            json=json.loads((data_root / "tree.json").read_text()),
        )
    )
    respx_mock.get("/repos/lsst/sdm_schemas").mock(
        return_value=httpx.Response(
            200,
            json=json.loads((data_root / "repo.json").read_text()),
        )
    )
    logger = structlog.get_logger()
    gh_client = GitHubAPI(http_client, "lsst-sqre")

    gh_repo = GitHubRepoStore(github_client=gh_client, logger=logger)
    tree = await gh_repo.get_recursive_git_tree(
        owner="lsst", repo="sdm_schemas", ref="w.2025.10"
    )
    assert tree.truncated is False
    schema_items = tree.glob("python/lsst/sdm/schemas/*.yaml")
    assert len(schema_items) == 15
    deployed_schemas_item = tree.get_path(
        "python/lsst/sdm/schemas/deployed-schemas.txt"
    )
    assert deployed_schemas_item.path == (
        "python/lsst/sdm/schemas/deployed-schemas.txt"
    )


@pytest.mark.asyncio
@pytest.mark.respx(base_url="https://api.github.com")
async def test_get_latest_release(
    respx_mock: respx.MockRouter, http_client: httpx.AsyncClient
) -> None:
    data_root = (
        Path(__file__).parent.parent / "data" / "github" / "sdm_schemas"
    )
    respx_mock.get("/repos/lsst/sdm_schemas/releases/latest").mock(
        return_value=httpx.Response(
            200,
            json=json.loads((data_root / "release.json").read_text()),
        )
    )
    logger = structlog.get_logger()
    gh_client = GitHubAPI(http_client, "lsst-sqre")

    gh_repo = GitHubRepoStore(github_client=gh_client, logger=logger)
    release = await gh_repo.get_latest_release(
        owner="lsst", repo="sdm_schemas"
    )
    assert release.tag_name == "w.2025.10"
    assert release.name == "w.2025.10"
    assert release.body == ""
    assert len(release.assets) == 2


@pytest.mark.asyncio
@pytest.mark.respx(base_url="https://api.github.com")
async def test_get_file_contents(
    respx_mock: respx.MockRouter, http_client: httpx.AsyncClient
) -> None:
    data_root = Path(__file__).parent.parent / "data" / "github" / "lsst-texmf"
    respx_mock.get(
        "/repos/lsst/lsst-texmf/contents/etc/authordb.yaml?ref=main"
    ).mock(
        return_value=httpx.Response(
            200,
            json=json.loads(
                (data_root / "authordb-contents.json").read_text()
            ),
        )
    )
    logger = structlog.get_logger()
    gh_client = GitHubAPI(http_client, "lsst-sqre")

    gh_repo = GitHubRepoStore(github_client=gh_client, logger=logger)
    contents = await gh_repo.get_file_contents(
        owner="lsst", repo="lsst-texmf", path="etc/authordb.yaml", ref="main"
    )
    assert contents.path == "etc/authordb.yaml"

    # Check that we can decode the contents
    authordb_yaml = yaml.safe_load(StringIO(contents.decode_content()))
    assert "authors" in authordb_yaml
