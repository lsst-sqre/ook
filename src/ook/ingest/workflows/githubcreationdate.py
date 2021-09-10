"""Workflow for extracting the creation date from a GitHub repository
that ignores any commits by a templatebot service.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import dateparser

from ook.github import (
    GitHubClientConfigError,
    create_github_installation_client_for_repository,
)

if TYPE_CHECKING:
    from aiohttp import web
    from structlog.stdlib import BoundLogger

__all__ = ["get_github_creation_date"]


async def get_github_creation_date(
    *,
    github_owner: str,
    github_repo: str,
    app: web.Application,
    logger: BoundLogger,
) -> Optional[datetime.datetime]:
    http_session = app["safir/http_session"]
    try:
        github_client = await create_github_installation_client_for_repository(
            http_client=http_session,
            config=app["safir/config"],
            owner=github_owner,
            repo=github_repo,
        )
        if github_client is None:
            return None
    except GitHubClientConfigError:
        return None

    historyquery = load_graphql_query("historyquery.graphql")
    response = await github_client.graphql(
        historyquery, owner=github_owner, name=github_repo
    )

    history = response["data"]["repository"]["defaultBranchRef"]["target"][
        "history"
    ]
    hasNextPage = history["pageInfo"]["hasNextPage"]
    if hasNextPage:
        logger.warning(
            "GitHub repository has a next page of history",
            owner=github_owner,
            github_repo=github_repo,
        )

    templatebot_names = ["SQuaRE Bot"]

    for commit in history["nodes"][::-1]:
        if commit["committer"] in templatebot_names:
            continue
        if commit["author"] in templatebot_names:
            continue

        creation_date = dateparser.parse(
            commit["authoredDate"], settings={"TIMEZONE": "UTC"}
        )
        return creation_date

    return None


def load_graphql_query(query_name: str) -> str:
    query_path = Path(__file__).parent / query_name
    return query_path.read_text()
