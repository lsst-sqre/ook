"""Access to GitHub repositories as data sources."""

from __future__ import annotations

from gidgethub.httpx import GitHubAPI
from safir.github.models import GitHubBlobModel, GitHubRepositoryModel
from structlog.stdlib import BoundLogger

from ._apimodels import (
    GitHubContents,
    GitHubReleaseModel,
    RecursiveGitTreeModel,
)

__all__ = ["GitHubRepoStore"]


class GitHubRepoStore:
    """A storage interface to a GitHub repository's contents and metadata."""

    def __init__(
        self, *, github_client: GitHubAPI, logger: BoundLogger
    ) -> None:
        self._gh = github_client
        self._logger = logger

    async def get_repo(
        self,
        *,
        owner: str,
        repo: str,
    ) -> GitHubRepositoryModel:
        """Get a repository.

        The repository model provides metadata about the repository and a rich
        set of URLs for interacting with the repository.

        https://docs.github.com/en/rest/reference/repos

        Parameters
        ----------
        owner
            The owner of the GitHub repository (user or organization).
        repo
            The name of the GitHub repository.

        Returns
        -------
        repository
            The repository metadata resource.
        """
        response = await self._gh.getitem(
            f"/repos/{owner}/{repo}", url_vars={"owner": owner, "repo": repo}
        )
        return GitHubRepositoryModel.model_validate(response)

    async def get_latest_release(
        self, *, owner: str, repo: str
    ) -> GitHubReleaseModel:
        """Get the latest release for a repository.

        https://docs.github.com/en/rest/releases/releases?apiVersion=2022-11-28#get-the-latest-release

        Parameters
        ----------
        owner
            The owner of the GitHub repository (user or organization).
        repo
            The name of the GitHub repository.

        Returns
        -------
        release
            The latest release of the repository.
        """
        response = await self._gh.getitem(
            f"/repos/{owner}/{repo}/releases/latest"
        )
        return GitHubReleaseModel.model_validate(response)

    async def get_release_by_tag(
        self, *, owner: str, repo: str, tag: str
    ) -> GitHubReleaseModel:
        """Get a release by its tag name.

        https://docs.github.com/en/rest/releases/releases?apiVersion=2022-11-28#get-a-release-by-tag-name
        """
        response = await self._gh.getitem(
            f"/repos/{owner}/{repo}/releases/tags/{tag}"
        )
        return GitHubReleaseModel.model_validate(response)

    async def get_recursive_git_tree(
        self, *, owner: str, repo: str, ref: str
    ) -> RecursiveGitTreeModel:
        """Get the recursive git tree of the repository from the GitHub API
        for this checkout's HEAD SHA (commit).

        Parameters
        ----------
        owner
            The owner of the GitHub repository (user or organization).
        repo
            The name of the GitHub repository.
        ref
            The tree ref to get the tree for. To get the root tree, use a
            git ref, such as a branch name or tag or commit SHA.

        Returns
        -------
        tree
            The contents of the repository's git tree.
        """
        repo_resource = await self.get_repo(owner=owner, repo=repo)
        response = await self._gh.getitem(
            repo_resource.trees_url + "{?recursive}",
            url_vars={"sha": ref, "recursive": "1"},
        )
        tree = RecursiveGitTreeModel.model_validate(response)
        if tree.truncated:
            self._logger.warning(
                "GitHub API repo treee is truncated",
                owner=owner,
                repo=repo,
                ref=ref,
            )
        return tree

    async def get_blob(self, blob_url: str) -> GitHubBlobModel:
        """Get a blob from the GitHub API.

        Parameters
        ----------
        blob_url
            The URL of the blob to get.

        Returns
        -------
        blob
            The blob data.
        """
        response = await self._gh.getitem(blob_url)
        return GitHubBlobModel.model_validate(response)

    async def get_file_contents(
        self, *, owner: str, repo: str, path: str, ref: str | None = None
    ) -> GitHubContents:
        """Get the contents of a file in a GitHub repository.

        Parameters
        ----------
        owner
            The owner of the GitHub repository (user or organization).
        repo
            The name of the GitHub repository.
        path
            The path to the file in the repository.
        ref
            The git reference to use for the repository. If not provided, the
            default branch is used.

        Returns
        -------
        GitHubContents
            The contents of the file.
        """
        url = "/repos/{owner}/{repo}/contents/{path}{?ref}"
        url_vars = {
            "owner": owner,
            "repo": repo,
            "path": path,
        }
        if ref is not None:
            url_vars["ref"] = str(ref)

        data = await self._gh.getitem(
            url,
            url_vars,  # type: ignore[arg-type]
            accept="application/vnd.github.object+json",
        )
        self._logger.debug(
            "Got file contents",
            owner=owner,
            repo=repo,
            path=path,
            ref=ref,
            data=data,
        )
        return GitHubContents.model_validate(data)

    async def get_raw_file_contents(
        self, *, owner: str, repo: str, path: str, ref: str | None = None
    ) -> str:
        """Get the raw contents of a file in a GitHub repository.

        Parameters
        ----------
        owner
            The owner of the GitHub repository (user or organization).
        repo
            The name of the GitHub repository.
        path
            The path to the file in the repository.
        ref
            The git reference to use for the repository. If not provided, the
            default branch is used.

        Returns
        -------
        str
            The raw contents of the file.
        """
        url = "/repos/{owner}/{repo}/contents/{path}{?ref}"
        url_vars = {
            "owner": owner,
            "repo": repo,
            "path": path,
        }
        if ref is not None:
            url_vars["ref"] = str(ref)

        return await self._gh.getitem(
            url,
            url_vars,  # type: ignore[arg-type]
            accept="application/vnd.github.raw+json",
        )
