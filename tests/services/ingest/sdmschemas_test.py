"""Tests for the SDM schemas ingest service."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import structlog
from gidgethub.httpx import GitHubAPI
from safir.github.models import GitHubBlobModel

from ook.services.ingest.sdmschemas import SdmSchemasIngestService
from ook.storage.github import GitHubRepoStore


@pytest.fixture
def github_repo_store(http_client: httpx.AsyncClient) -> GitHubRepoStore:
    logger = structlog.get_logger("ook")
    return GitHubRepoStore(
        github_client=GitHubAPI(http_client, "lsst-sqre"),
        logger=logger,
    )


@pytest.mark.asyncio
async def test_load_schema(
    http_client: httpx.AsyncClient, github_repo_store: GitHubRepoStore
) -> None:
    logger = structlog.get_logger("ook")
    ingest_service = SdmSchemasIngestService(
        logger=logger,
        http_client=http_client,
        github_repo_store=github_repo_store,
    )

    schema_yaml = (
        Path(__file__).parent.parent.parent
        / "data"
        / "github"
        / "sdm_schemas"
        / "dp02_dc.yaml"
    ).read_text()
    schemas = list(
        ingest_service._load_schema(
            docs_url="https://sdm-schemas.lsst.io/dp02.html",
            yaml_content=schema_yaml,
        )
    )
    assert len(schemas) > 0


@pytest.mark.asyncio
async def test_list_deployed_schemas(
    http_client: httpx.AsyncClient, github_repo_store: GitHubRepoStore
) -> None:
    logger = structlog.get_logger("ook")
    ingest_service = SdmSchemasIngestService(
        logger=logger,
        http_client=http_client,
        github_repo_store=github_repo_store,
    )
    deployed_schemas_blob = GitHubBlobModel.model_validate_json(
        (
            Path(__file__).parent.parent.parent
            / "data"
            / "github"
            / "sdm_schemas"
            / "deployed_schemas_blob.json"
        ).read_text()
    )
    deployed_schemas = ingest_service._list_deployed_schemas(
        deployed_schemas_blob
    )
    assert deployed_schemas == [
        "dp02_dc2.yaml",
        "dp02_obscore.yaml",
        "dp03_10yr.yaml",
        "dp03_1yr.yaml",
    ]
