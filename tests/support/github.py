"""Mock the GitHub API client."""

from __future__ import annotations

import json
from pathlib import Path

import respx

# This mock is adapted for lsst-sqre/mobu.

__all__ = ["GitHubMocker"]

GITHUB_DATA_DIR = Path(__file__).parent.parent / "data" / "github"


class GitHubMocker:
    """Mock responses from the GitHub API."""

    def __init__(self) -> None:
        self.router = respx.mock(
            base_url="https://api.github.com",
            assert_all_mocked=True,
            assert_all_called=False,
        )

        # Mock the endpoint that gives us a token
        self.router.post(
            url__regex=r"/app/installations/(?P<installation_id>\d+)/access_tokens",
        ).respond(
            json={
                "token": "whatever",
                "expires_at": "whenever",
            }
        )

        self.router.get(
            url__regex=r"/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/installation",
        ).respond(json={"id": 12345})

    def mock_sdm_schema_release_ingest(
        self, owner: str = "lsst", repo: str = "sdm_schemas"
    ) -> None:
        """Mock the GitHub interactions used by
        SdmSchemasIngestService.ingest().
        """
        sdm_data_dir = GITHUB_DATA_DIR / "sdm_schemas"
        release_data = (sdm_data_dir / "release.json").read_text()
        self.router.get(
            url=f"/repos/{owner}/{repo}/releases/latest",
        ).respond(
            json=json.loads(release_data),
        )

        # Mock getting repo
        repo_data = (sdm_data_dir / "repo.json").read_text()
        self.router.get(
            url=f"/repos/{owner}/{repo}",
        ).respond(
            json=json.loads(repo_data),
        )

        # Mock get_recursive_git_tree
        tree_data = (sdm_data_dir / "tree.json").read_text()
        self.router.get(
            url=f"/repos/{owner}/{repo}/git/trees/w.2025.10?recursive=1"
        ).respond(
            json=json.loads(tree_data),
        )

        # Mock getting the blob for deployed-schemas.txt
        blob_data = (sdm_data_dir / "deployed_schemas_blob.json").read_text()
        self.router.get(
            url="/repos/lsst/sdm_schemas/git/blobs/0c9b215caeddf9e32dad6c7d26fb067fd81e4a98"
        ).respond(
            json=json.loads(blob_data),
        )

        # Mock getting the blob for dp02_dc2_blob.json
        blob_data = (sdm_data_dir / "dp02_dc2_blob.json").read_text()
        self.router.get(
            url="/repos/lsst/sdm_schemas/git/blobs/0ae398f33adf59e647d766eabd5a3cc0f592026e"
        ).respond(
            json=json.loads(blob_data),
        )

        # Mock getting the blob for dp02_obscore_blob.json
        blob_data = (sdm_data_dir / "dp02_obscore_blob.json").read_text()
        self.router.get(
            url="/repos/lsst/sdm_schemas/git/blobs/9a18717c8a1a7d0f71d989558f621ad59718487d"
        ).respond(
            json=json.loads(blob_data),
        )

        # Mock getting the blob for dp03_10yr_blob.json
        blob_data = (sdm_data_dir / "dp03_10yr_blob.json").read_text()
        self.router.get(
            url="/repos/lsst/sdm_schemas/git/blobs/9049111d8d24f455b96bb96f70dd521aee5c0e7c"
        ).respond(
            json=json.loads(blob_data),
        )

        # Mock getting the blob for dp03_1yr_blob.json
        blob_data = (sdm_data_dir / "dp03_1yr_blob.json").read_text()
        self.router.get(
            url="/repos/lsst/sdm_schemas/git/blobs/e8d4603384bfecbb41b4a6958211385d8de7412b"
        ).respond(
            json=json.loads(blob_data),
        )

        # Mock getting the blob dp02_md_blob.json
        blob_data = (sdm_data_dir / "dp02_md_blob.json").read_text()
        self.router.get(
            url="/repos/lsst/sdm_schemas/git/blobs/6f7ae30cf2e14075a479164d2d23fc0a046a78bd"
        ).respond(
            json=json.loads(blob_data),
        )

        # Mock getting the blob dp03_md_blob.json
        blob_data = (sdm_data_dir / "dp03_md_blob.json").read_text()
        self.router.get(
            url="/repos/lsst/sdm_schemas/git/blobs/d58c2ca33825ef17f13d1846f0d51b17eecfec7d"
        ).respond(
            json=json.loads(blob_data),
        )
