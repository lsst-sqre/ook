"""GitHub Repository metadata retrival service."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import dateparser
from safir.github import GitHubAppClientFactory
from structlog.stdlib import BoundLogger


class GitHubMetadataService:
    """A service for retrieving GitHub repository metadata.

    Parameters
    ----------
    gh_factory
        A factory for creating GitHub API clients.
    logger
        A logger for the service.
    """

    def __init__(
        self, *, gh_factory: GitHubAppClientFactory, logger: BoundLogger
    ) -> None:
        self._logger = logger
        self._gh_factory = gh_factory

    async def get_repo_first_commit_date(
        self, *, owner: str, repo: str
    ) -> datetime | None:
        """Get the datetime of the first commit on the default branch of a
        GitHub repository.

        Parameters
        ----------
        owner
            The owner of the GitHub repository (user or organization).
        repo
            The name of the GitHub repository.

        Returns
        -------
        datetime.datetime
            The creation date of the repository.
        """

        def _parse_commit_date(query_result: Any) -> datetime | None:
            commit_node = query_result["repository"]["defaultBranchRef"][
                "target"
            ]["history"]["nodes"][0]
            return dateparser.parse(
                commit_node["authoredDate"], settings={"TIMEZONE": "UTC"}
            )

        gh_client = await self._gh_factory.create_installation_client_for_repo(
            owner=owner, repo=repo
        )
        query = self.load_graphql_query("commithistory.graphql")
        result = await gh_client.graphql(
            query, owner=owner, name=repo, cursor=None
        )
        commit_count = result["repository"]["defaultBranchRef"]["target"][
            "history"
        ]["totalCount"]

        if commit_count < 1:
            return None
        elif commit_count == 1:
            return _parse_commit_date(result)
        else:
            # The initial query shows us how many commits there are. We can
            # use that to form a cursor to make a second query for the first
            # commit.
            # The endCursor looks like this:
            #   eec9ebf34b2b002220395b8ef0ee0fbe6e936d6d 21
            end_cursor = result["repository"]["defaultBranchRef"]["target"][
                "history"
            ]["pageInfo"]["endCursor"]
            commit_ref = end_cursor.split(" ")[0]
            # cursor is for the commit *before* the first commit
            cursor = f"{commit_ref} {commit_count - 2}"
            second_result = await gh_client.graphql(
                query, owner=owner, name=repo, cursor=cursor
            )
            return _parse_commit_date(second_result)

    @staticmethod
    def parse_repo_from_github_url(url: str) -> tuple[str, str]:
        """Parse the repo owner and name from a GitHub URL.

        Parameters
        ----------
        url
            The GitHub URL to parse
            (e.g. "https://github.com/lsst-sqre/sqr-000").

        Returns
        -------
        tuple[str, str]
            The repo owner and name.
        """
        m = re.search(r"github\.com/(?P<owner>[^\./]+)/(?P<name>[^\./]+)", url)
        if m:
            return (m.group("owner"), m.group("name"))
        else:
            raise ValueError(f"Url {url} is not a GitHub repo")

    @staticmethod
    def format_raw_content_url(
        *, owner: str, repo: str, path: str, git_ref: str
    ) -> str:
        """Format a GitHub repository raw content URL.

        Parameters
        ----------
        owner
            The owner of the GitHub repository (user or organization).
        repo
            The name of the GitHub repository.
        path
            The path to the file in the repository.
        git_ref
            The Git ref (branch, tag, or commit SHA) to use.

        Returns
        -------
        str
            The formatted URL.
        """
        return (
            "https://raw.githubusercontent.com/"
            f"{owner}/{repo}/{git_ref}/{path}"
        )

    def load_graphql_query(self, query_name: str) -> str:
        """Load a GitHub GraphQL query from the GitHub service's module."""
        query_path = Path(__file__).parent / query_name
        return query_path.read_text()
