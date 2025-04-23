"""Storage to the lsst/lsst-texmf GitHub repository."""

from __future__ import annotations

from io import StringIO

import yaml
from pydantic import BaseModel, Field
from safir.github import GitHubAppClientFactory
from structlog.stdlib import BoundLogger

from ook.storage.github import GitHubRepoStore


class LsstTexmfGitHubRepo:
    """Storage interface to the lsst/lsst-texmf GitHub repository."""

    def __init__(
        self,
        *,
        logger: BoundLogger,
        repo_client: GitHubRepoStore,
        github_owner: str = "lsst",
        github_repo: str = "lsst-texmf",
        git_ref: str,
    ) -> None:
        self._logger = logger
        self._repo_client = repo_client
        self._gh_repo = {"owner": github_owner, "repo": github_repo}
        self._git_ref = git_ref

    @classmethod
    async def create_with_default_branch(
        cls,
        *,
        logger: BoundLogger,
        gh_factory: GitHubAppClientFactory,
        github_owner: str = "lsst",
        github_repo: str = "lsst-texmf",
    ) -> LsstTexmfGitHubRepo:
        """Create a new storage interface to the lsst/lsst-texmf GitHub
        repository using the default branch.
        """
        gh_client = await gh_factory.create_installation_client_for_repo(
            owner=github_owner, repo=github_repo
        )

        repo_client = GitHubRepoStore(github_client=gh_client, logger=logger)

        # Get the default branch from the repository
        repo_info = await repo_client.get_repo(
            owner=github_owner, repo=github_repo
        )
        default_branch = repo_info.default_branch

        return cls(
            logger=logger,
            repo_client=repo_client,
            github_owner=github_owner,
            github_repo=github_repo,
            git_ref=default_branch,
        )

    async def load_authordb(self) -> AuthorDbYaml:
        """Load the authordb.yaml file."""
        # Get the contents of the authordb.yaml file
        authordb_path = "etc/authordb.yaml"
        file_contents = await self._repo_client.get_file_contents(
            owner=self._gh_repo["owner"],
            repo=self._gh_repo["repo"],
            path=authordb_path,
            ref=self._git_ref,
        )
        authordb_yaml = yaml.safe_load(
            StringIO(file_contents.decode_content())
        )
        return AuthorDbYaml.model_validate(authordb_yaml)


class AuthorDbAuthor(BaseModel):
    """Model for an author entry in the authordb.yaml file."""

    name: str = Field(description="Author's surname.")

    initials: str = Field(description="Author's given name.")

    affil: list[str] = Field(
        default_factory=list, description="Affiliation IDs"
    )

    alt_affil: list[str] = Field(
        default_factory=list, description=("Alternative affiliations / notes.")
    )

    orcid: str | None = Field(
        default=None,
        description="Author's ORCiD identifier (optional)",
    )

    email: str | None = Field(
        default=None,
        description=(
            "Author's email username (if using a known email provider given "
            "their affiliation ID) or ``username@provider`` (to specify the "
            "provider) or their full email address."
        ),
    )

    @property
    def is_collaboration(self) -> bool:
        """Check if the author is a collaboration."""
        return self.initials == "" and self.affil == ["_"]


class AuthorDbYaml(BaseModel):
    """Model for the authordb.yaml file in lsst/lsst-texmf."""

    affiliations: dict[str, str] = Field(
        description=(
            "Mapping of affiliation IDs to affiliation info. Affiliations "
            "are their name, a comma, and their address."
        )
    )

    emails: dict[str, str] = Field(
        description=("Mapping of affiliation IDs to email domains.")
    )

    authors: dict[str, AuthorDbAuthor] = Field(
        description="Mapping of author IDs to author information"
    )
