"""Utilities library."""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Coroutine,
    Dict,
    Optional,
    TypeVar,
)

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
    encoding: Optional[str] = None,
    content_type: Optional[str] = "application/json",
) -> Dict[str, Any]:
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
    """Create a URL for raw GitHub content (raw.githubusercontent.com)"""
    if file_path.startswith("/"):
        file_path = file_path.lstrip("/")
    if repo_path.startswith("/"):
        repo_path = repo_path.lstrip("/")
    if repo_path.endswith("/"):
        repo_path = repo_path.rstrip("/")

    return (
        f"https://raw.githubusercontent.com/{repo_path}/{git_ref}/{file_path}"
    )


# FIXME this is vendored from Safir 2; use that when Ook becomes a FastAPI app.
def run_with_asyncio(
    f: Callable[..., Coroutine[Any, Any, T]]
) -> Callable[..., T]:
    """Run the decorated function with `asyncio.run`.
    Intended to be used as a decorator around an async function that needs to
    be run in a sync context.  The decorated function will be run with
    `asyncio.run` when invoked.  The caller must not already be inside an
    asyncio task.

    Parameters
    ----------
    f
        The function to wrap.

    Examples
    --------
    An application that uses Safir and `Click`_ may use the following Click
    command function to initialize a database.

    .. code-block:: python
       import structlog
       from safir.asyncio import run_with_asyncio
       from safir.database import initialize_database
       from .config import config
       from .schema import Base
       @main.command()
       @run_with_asyncio
       async def init() -> None:
           logger = structlog.get_logger(config.safir.logger_name)
           engine = await initialize_database(
               config.database_url,
               config.database_password,
               logger,
               schema=Base.metadata,
           )
           await engine.dispose()
    """

    @wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        return asyncio.run(f(*args, **kwargs))

    return wrapper
