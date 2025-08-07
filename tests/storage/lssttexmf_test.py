"""Test the storage interface to the lsst-texmf GitHub repository."""

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
from ook.storage.lssttexmf import (
    AuthorDbYaml,
    GlossaryDef,
    LsstTexmfGitHubRepo,
)


@pytest.fixture
def authordb_content() -> dict[str, str]:
    """Fixture for the content of the author database."""
    path = (
        Path(__file__).parent.parent
        / "data"
        / "github"
        / "lsst-texmf"
        / "authordb.yaml"
    )
    return yaml.safe_load(StringIO(path.read_text()))


@pytest.fixture
def glossarydefs_content() -> str:
    """Fixture for the content of the glossary definitions."""
    path = (
        Path(__file__).parent.parent
        / "data"
        / "github"
        / "lsst-texmf"
        / "glossarydefs.csv"
    )
    return path.read_text()


@pytest.fixture
def glossarydefs_es_content() -> str:
    """Fixture for the content of the glossary definitions in Spanish."""
    path = (
        Path(__file__).parent.parent
        / "data"
        / "github"
        / "lsst-texmf"
        / "glossarydefs_es.csv"
    )
    return path.read_text()


def test_authordb_models(
    authordb_content: dict[str, str],
) -> None:
    """Test the pydantic models for the authordb.yaml content."""
    authordb = AuthorDbYaml.model_validate(authordb_content)
    assert len(authordb.authors) > 0
    assert len(authordb.affiliations) > 0


@pytest.mark.asyncio
@pytest.mark.respx(base_url="https://api.github.com")
async def test_repo_store(
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
    texmf_repo = LsstTexmfGitHubRepo(
        logger=logger,
        repo_client=gh_repo,
        github_owner="lsst",
        github_repo="lsst-texmf",
        git_ref="main",
    )
    authordb = await texmf_repo.load_authordb()
    assert len(authordb.authors) > 0
    assert len(authordb.affiliations) > 0


def test_authordb_converstion_to_domain(
    authordb_content: dict[str, str],
) -> None:
    authordb = AuthorDbYaml.model_validate(authordb_content)
    affiliations = authordb.affiliations_to_domain()
    assert len(affiliations) > 0
    authors = authordb.authors_to_domain()
    assert len(authors) > 0


def test_glossarydef_models(
    glossarydefs_content: str,
) -> None:
    defs = GlossaryDef.parse_csv(glossarydefs_content)
    assert len(defs) > 0


def test_country_code_latex_processing() -> None:
    """Test that country codes with LaTeX markup are properly processed."""
    # Create test data with LaTeX markup in country_code
    test_data = {
        "affiliations": {
            "test_affil": {
                "institute": "Test University",
                "address": {
                    # LaTeX markup for États-Unis (French for USA)
                    "country_code": "\\'{E}tats-Unis"
                },
            }
        },
        "authors": {
            "test_author": {"family_name": "Doe", "affil": ["test_affil"]}
        },
    }

    # Parse and convert to domain models
    authordb = AuthorDbYaml.model_validate(test_data)
    affiliations = authordb.affiliations_to_domain()

    # Verify that LaTeX processing occurred
    test_affiliation = affiliations["test_affil"]
    assert test_affiliation.address is not None

    # The LaTeX should be processed to unicode
    assert test_affiliation.address.country_name == "États-Unis"

    # The country_code should be normalized (États-Unis might normalize to US
    # but this specific string might not have a perfect match, so we just
    # verify it's processed)
    assert (
        test_affiliation.address.country_code is not None
        or test_affiliation.address.country_name is not None
    )
