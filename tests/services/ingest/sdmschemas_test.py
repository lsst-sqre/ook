"""Tests for the SDM schemas ingest service."""

from __future__ import annotations

from pathlib import Path

import pytest
from safir.github.models import GitHubBlobModel

from ook.factory import Factory


@pytest.mark.asyncio
async def test_load_schema(factory: Factory) -> None:
    ingest_service = await factory.create_sdm_schemas_ingest_service()

    schema_yaml = (
        Path(__file__).parent.parent.parent
        / "data"
        / "github"
        / "sdm_schemas"
        / "dp02_dc.yaml"
    ).read_text()
    schema = ingest_service._load_schema(
        yaml_content=schema_yaml,
        github_owner="lsst",
        github_repo="sdm_schemas",
        github_ref="w.2025.04",
        github_path="python/lsst/sdm/schemas/dp02_dc.yaml",
    )
    assert schema.name == "dp02_dc2_catalogs"
    assert schema.tables is not None
    assert len(schema.tables) > 0


@pytest.mark.asyncio
async def test_list_deployed_schemas(factory: Factory) -> None:
    ingest_service = await factory.create_sdm_schemas_ingest_service()

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
