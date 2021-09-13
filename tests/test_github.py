from __future__ import annotations

import pytest

from ook.github import parse_repo_from_github_url


@pytest.mark.parametrize(
    "url,expected_owner,expected_name",
    [
        ("https://github.com/lsst-sqre/sqr-020", "lsst-sqre", "sqr-020"),
        ("https://github.com/lsst-sqre/sqr-020.git", "lsst-sqre", "sqr-020"),
        (
            "https://github.com/lsst-sqre/sqr-020/settings",
            "lsst-sqre",
            "sqr-020",
        ),
    ],
)
def test_parse_repo_url(
    url: str, expected_owner: str, expected_name: str
) -> None:
    owner, name = parse_repo_from_github_url(url)
    assert owner == expected_owner
    assert name == expected_name
