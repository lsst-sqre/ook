"""Mock the GitHub API client."""

from __future__ import annotations

import respx

# This mock is adapted for lsst-sqre/mobu.

__all__ = ["GitHubMocker"]


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
