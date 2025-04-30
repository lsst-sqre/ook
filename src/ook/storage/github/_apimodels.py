"""GitHub API Models."""

# Our primary goal is to share models through the safir.github package, but
# this module provides a convenient place to define them before upstreaming.

from __future__ import annotations

import base64
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl

__all__ = [
    "GitHubContents",
    "GitHubReleaseAssetModel",
    "GitHubReleaseModel",
    "GitTreeItem",
    "GitTreeMode",
    "RecursiveGitTreeModel",
]


class GitTreeMode(str, Enum):
    """Git tree mode values."""

    file = "100644"
    executable = "100755"
    directory = "040000"
    submodule = "160000"
    symlink = "120000"


class GitTreeItem(BaseModel):
    """A Pydantic model for a single item in the response parsed by
    `RecursiveGitTreeModel`.
    """

    path: Annotated[str, Field(title="Path to the item in the repository")]

    mode: Annotated[GitTreeMode, Field(title="Mode of the item.")]

    sha: Annotated[str, Field(title="Git sha of tree object")]

    url: Annotated[HttpUrl, Field(title="URL of the object")]

    def match_glob(self, pattern: str) -> bool:
        """Test if this path matches a glob pattern."""
        p = PurePosixPath(self.path)
        return p.match(pattern)

    @property
    def path_extension(self) -> str:
        p = PurePosixPath(self.path)
        return p.suffix

    @property
    def path_stem(self) -> str:
        """The filepath, without the suffix."""
        return self.path[: -len(self.path_extension)]


class RecursiveGitTreeModel(BaseModel):
    """A Pydantic model for the output of ``GET api.github.com/repos/{owner}/
    {repo}/git/trees/{sha}?recursive=1`` for a git commit, which describes
    the full contents of a GitHub repository.
    """

    sha: Annotated[str, Field(title="SHA of the commit.")]

    url: Annotated[HttpUrl, Field(title="GitHub API URL of this resource")]

    tree: Annotated[list[GitTreeItem], Field(title="Items in the git tree")]

    truncated: Annotated[
        bool,
        Field(title="True if the dataset does not contain the whole repo"),
    ]

    def glob(self, pattern: str) -> list[GitTreeItem]:
        """Return a list of items that match a glob pattern."""
        return [item for item in self.tree if item.match_glob(pattern)]

    def get_path(self, path: str) -> GitTreeItem:
        """Get a single item by path.

        Returns
        -------
        item
            The item in the tree.

        Raises
        ------
        ValueError
            If the path is not found in the tree.
        """
        try:
            return next(item for item in self.tree if item.path == path)
        except StopIteration as e:
            raise ValueError(f"Path {path} not found in tree") from e


class GitHubReleaseAssetModel(BaseModel):
    """A Pydantic model for a GitHub release asset."""

    url: Annotated[HttpUrl, Field(title="API URL of the asset")]

    browser_download_url: Annotated[
        HttpUrl, Field(title="URL to download the asset")
    ]

    id: Annotated[int, Field(title="Asset ID")]

    name: Annotated[str, Field(title="Asset name")]

    label: Annotated[str, Field(title="Asset label")]

    state: Annotated[str, Field(title="Asset state")]

    content_type: Annotated[str, Field(title="Asset content type")]

    size: Annotated[int, Field(title="Asset size")]

    download_count: Annotated[int, Field(title="Asset download count")]

    created_at: Annotated[datetime, Field(title="Asset creation time")]

    updated_at: Annotated[datetime, Field(title="Asset update time")]


class GitHubReleaseModel(BaseModel):
    """A Pydantic model for a GitHub release.

    https://docs.github.com/en/rest/releases/releases?apiVersion=2022-11-28#get-the-latest-release
    """

    url: Annotated[HttpUrl, Field(title="API URL of the release")]

    html_url: Annotated[HttpUrl, Field(title="Web page URL of the release")]

    assets_url: Annotated[HttpUrl, Field(title="API URL of release assets")]

    upload_url: Annotated[HttpUrl, Field(title="API URL to upload assets")]

    tarball_url: Annotated[HttpUrl, Field(title="URL to download the tarball")]

    zipball_url: Annotated[HttpUrl, Field(title="URL to download the zipball")]

    tag_name: Annotated[str, Field(title="Git tag")]

    target_commitish: Annotated[
        str,
        Field(
            title="Commitish target",
            description=(
                "The commitish value that determines where the Git tag is "
                "created from. Can be any branch or commit SHA."
            ),
            examples=["main"],
        ),
    ]

    name: Annotated[str | None, Field(title="The name of the release")]

    body: Annotated[str | None, Field(title="The release body")]

    draft: Annotated[bool, Field(title="True if the release is a draft")]

    prerelease: Annotated[
        bool, Field(title="True if the release is a prerelease")
    ]

    created_at: Annotated[
        datetime, Field(title="The date the release was created")
    ]

    published_at: Annotated[
        datetime | None, Field(title="The date the release was published")
    ]

    assets: Annotated[list[dict], Field(title="Release assets")]


class GitHubContents(BaseModel):
    """A Pydantic model for a GitHub contents (repository contents API).

    https://docs.github.com/en/rest/repos/contents?apiVersion=2022-11-28#get-repository-content
    """

    type: Annotated[
        str,
        Field(
            description=(
                "The type of the file. Can be one of 'file', 'dir', or "
                "'symlink'."
            ),
        ),
    ]

    size: Annotated[
        int,
        Field(
            description="The size of the file in bytes.",
        ),
    ]

    name: Annotated[
        str,
        Field(
            description="The name of the file.",
        ),
    ]

    path: Annotated[
        str,
        Field(
            description="The path to the file in the repository.",
        ),
    ]

    sha: Annotated[
        str,
        Field(
            description="The SHA of the file.",
        ),
    ]
    url: Annotated[
        HttpUrl,
        Field(
            description="The URL of the file.",
        ),
    ]

    git_url: Annotated[
        HttpUrl,
        Field(
            description="The Git URL of the file.",
        ),
    ]

    html_url: Annotated[
        HttpUrl,
        Field(
            description="The HTML URL of the file.",
        ),
    ]

    download_url: Annotated[
        HttpUrl,
        Field(
            description="The download URL of the file.",
        ),
    ]

    content: Annotated[
        str | None,
        Field(
            description="The content of the file, base64 encoded.",
        ),
    ] = None

    encoding: Annotated[
        str | None,
        Field(
            description=(
                "The encoding of the file. Is base64 if the content is "
                "present."
            )
        ),
    ] = None

    entries: Annotated[
        list[GitHubContents] | None,
        Field(
            description=(
                "The entries in the directory. Only present if the type is "
                "'dir'."
            ),
        ),
    ] = None

    def decode_content(self) -> str | None:
        """Decode the content of the file.

        Returns
        -------
        str | None
            The decoded content of the file, or None if the content is not
            present.
        """
        if self.content is None:
            return None
        if self.encoding == "base64":
            return base64.b64decode(self.content).decode("utf-8")
        return self.content
