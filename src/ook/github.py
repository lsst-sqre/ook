"""GitHub App client."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional, Tuple

import gidgethub
import gidgethub.apps
from gidgethub.aiohttp import GitHubAPI

if TYPE_CHECKING:
    import aiohttp.ClientSession

    from ook.config import Configuration

__all__ = [
    "GitHubClientConfigError",
    "get_app_jwt",
    "create_github_installation_client",
    "get_repository_installation_id",
    "create_github_installation_client_for_repository",
]


class GitHubClientConfigError(Exception):
    """Raised if there is an error with the GitHub App's configuration."""


def get_app_jwt(config: Configuration) -> str:
    """Create the GitHub App's JWT based on application configuration.

    This token is for authenticating as the GitHub App itself, as opposed to
    an installation of the app. See
    https://docs.github.com/en/developers/apps/building-github-apps/authenticating-with-github-apps#authenticating-as-a-github-app

    Parameters
    ----------
    http_client : aiohttp.ClientSession
        The http client.
    config : ook.config.Configuration
        The app configuration

    Returns
    -------
    str
        The JWT token.

    Raises
    ------
    GitHubClientError
        Raised if there is an issue with the GitHub App configuration.
    """
    if config.github_app_private_key is None:
        raise GitHubClientConfigError(
            "The GitHub app private key is not configured."
        )
    private_key = config.github_app_private_key.get_secret_value()

    if config.github_app_id is None:
        raise GitHubClientConfigError("The GitHub app id is not configured.")
    app_id = config.github_app_id

    return gidgethub.apps.get_jwt(app_id=app_id, private_key=private_key)


def create_github_client(
    *,
    http_client: aiohttp.ClientSession,
    config: Configuration,
    oauth_token: Optional[str] = None,
) -> GitHubAPI:
    """Create a GitHub client.

    Parameters
    ----------
    http_client : aiohttp.ClientSession
        The http client.
    config : ook.config.Configuration
        The app configuration
    oauth_token : str, optional
        If set, the client is authenticated with the oauth token (common
        for clients acting as an app installation or on behalf of a user).

    Returns
    -------
    gidgethub.aiohttp.GitHubAPI
    """
    return GitHubAPI(http_client, "lsst-sqre/ook", oauth_token=oauth_token)


async def create_github_installation_client(
    *,
    http_client: aiohttp.ClientSession,
    config: Configuration,
    installation_id: str,
) -> GitHubAPI:
    """Create a GitHub API client authorized as a GitHub App installation,
    with specific permissions on the repository/organization the app is
    installed into.

    Parameters
    ----------
    http_client : aiohttp.ClientSession
        The http client.
    config : ook.config.Configuration
        The app configuration
    installation_id : str
        The installation ID, often obtained from a webhook payload
        (``installation.id`` path), or from the ``id`` key returned by
        `iter_installations`.

    Returns
    -------
    gidgethub.aiohttp.ClientSession
        The GitHub client with an embedded OAuth token that authenticates
        all requests as the GitHub app installation.
    """
    if config.github_app_id is None:
        raise GitHubClientConfigError("The GitHub app id is not configured.")
    app_id = config.github_app_id

    if config.github_app_private_key is None:
        raise GitHubClientConfigError(
            "The GitHub app private key is not configured."
        )
    private_key = config.github_app_private_key.get_secret_value()

    anon_gh_app = create_github_client(http_client=http_client, config=config)
    token_info = await gidgethub.apps.get_installation_access_token(
        anon_gh_app,
        installation_id=installation_id,
        app_id=app_id,
        private_key=private_key,
    )
    token = token_info["token"]

    # Generate a new client with the embedded OAuth token.
    gh = create_github_client(
        http_client=http_client, oauth_token=token, config=config
    )
    return gh


async def get_repository_installation_id(
    *,
    http_client: aiohttp.ClientSession,
    config: Configuration,
    owner: str,
    repo: str,
) -> Optional[str]:
    """Get the app installation that has access to a given repository,
    or None if the installation does not exist.
    """
    jwt = get_app_jwt(config=config)
    app_client = create_github_client(http_client=http_client, config=config)
    try:
        data = await app_client.getitem(
            "/repos/{owner}/{repo}/installation",
            jwt=jwt,
            url_vars={"owner": owner, "repo": repo},
        )
    except gidgethub.HTTPException:
        return None

    return data["id"]


async def create_github_installation_client_for_repository(
    *,
    http_client: aiohttp.ClientSession,
    config: Configuration,
    owner: str,
    repo: str,
) -> Optional[GitHubAPI]:
    installation_id = await get_repository_installation_id(
        http_client=http_client, config=config, owner=owner, repo=repo
    )
    if installation_id is None:
        return None

    return await create_github_installation_client(
        http_client=http_client, config=config, installation_id=installation_id
    )


def parse_repo_from_github_url(url: str) -> Tuple[str, str]:
    """Parse the repo owner and name from a GitHub URL."""
    m = re.search(r"github\.com/(?P<owner>[^\./]+)/(?P<name>[^\./]+)", url)
    if m:
        return (m.group("owner"), m.group("name"))
    else:
        raise ValueError(f"Url {url} is not a GitHub repo")
