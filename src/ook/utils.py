"""Utilities library."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

T = TypeVar("T")

if TYPE_CHECKING:
    from aiohttp import ClientSession
    from structlog.stdlib import BoundLogger

__all__ = ["get_html_content", "get_json_data", "make_raw_github_url"]


async def get_html_content(
    *, url: str, http_session: ClientSession, logger: BoundLogger
) -> str:
    """Get HTML content from a URL."""
    html_content_response = await http_session.get(url)
    if html_content_response.status != 200:
        raise RuntimeError(
            f"Could not download {url}."
            f"Got status {html_content_response.status}."
        )
    return await html_content_response.text()


async def get_json_data(
    *,
    url: str,
    http_session: ClientSession,
    logger: BoundLogger,
    encoding: str | None = None,
    content_type: str | None = "application/json",
) -> dict[str, Any]:
    """Get and parse JSON content from a URL."""
    response = await http_session.get(url)
    if response.status != 200:
        raise RuntimeError(
            f"Could not download {url}." f"Got status {response.status}."
        )
    return await response.json(encoding=encoding, content_type=content_type)


def make_raw_github_url(
    *, repo_path: str, git_ref: str, file_path: str
) -> str:
    """Create a URL for raw GitHub content (raw.githubusercontent.com)."""
    if file_path.startswith("/"):
        file_path = file_path.lstrip("/")
    if repo_path.startswith("/"):
        repo_path = repo_path.lstrip("/")
    if repo_path.endswith("/"):
        repo_path = repo_path.rstrip("/")

    return (
        f"https://raw.githubusercontent.com/{repo_path}/{git_ref}/{file_path}"
    )
