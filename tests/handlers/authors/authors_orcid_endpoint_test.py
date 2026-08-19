"""Tests for the ORCID lookup mode of the /ook/authors endpoint."""

from __future__ import annotations

from urllib.parse import parse_qs, quote, urlparse

import pytest
import pytest_asyncio
from httpx import AsyncClient
from safir.http import PaginationLinkData

from tests.support.github import GitHubMocker

SICKJ_ORCID = "0000-0003-3001-676X"


@pytest_asyncio.fixture
async def ingest_lsst_texmf(
    client: AsyncClient, mock_github: GitHubMocker
) -> None:
    """Ingest lsst-texmf data for testing."""
    mock_github.mock_lsst_texmf_ingest()

    response = await client.post(
        "/ook/ingest/lsst-texmf",
        json={"ingest_authordb": True, "ingest_glossary": False},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_author_by_orcid(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """An ORCID lookup returns the same record as the internal-ID path."""
    response = await client.get(f"/ook/authors?orcid={SICKJ_ORCID}")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["internal_id"] == "sickj"
    assert data[0]["orcid"] == f"https://orcid.org/{SICKJ_ORCID}"
    assert "score" not in data[0]

    by_id_response = await client.get("/ook/authors/sickj")
    assert by_id_response.status_code == 200
    assert data[0] == by_id_response.json()
    assert data[0]["affiliations"][0]["name"] == "J.Sick Codes Inc."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "orcid",
    [
        "0000-0003-3001-676x",
        "https://orcid.org/0000-0003-3001-676X",
        "http://orcid.org/0000-0003-3001-676X",
        "orcid.org/0000-0003-3001-676X",
        "https://www.orcid.org/0000-0003-3001-676X",
        "https://orcid.org/0000-0003-3001-676X/",
        " 0000-0003-3001-676X ",
    ],
)
async def test_get_author_by_orcid_spellings(
    client: AsyncClient, ingest_lsst_texmf: None, orcid: str
) -> None:
    """Every accepted spelling resolves to the same body."""
    canonical = await client.get(f"/ook/authors?orcid={SICKJ_ORCID}")
    assert canonical.status_code == 200

    response = await client.get(f"/ook/authors?orcid={quote(orcid)}")
    assert response.status_code == 200
    assert response.json() == canonical.json()


@pytest.mark.asyncio
async def test_get_author_by_orcid_miss(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """A well-formed ORCID nobody holds is an empty list, not a 404."""
    response = await client.get("/ook/authors?orcid=0000-0001-2345-6789")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "orcid",
    [
        "Jonathan Sick",
        "https://example.com/0000-0003-3001-676X",
        "0000-0003-3001-6760",  # valid shape, wrong check digit
        "000000033001676X",  # hyphen-less compact form
        "0000-0003-3001-676",  # too short
        # Fullwidth digits are not ORCID digits: a 422, not a silent empty
        # list from a lookup that could never match a canonical stored value.
        "\uff10\uff10\uff10\uff10-\uff10\uff10\uff10\uff13-\uff13\uff10\uff10\uff11-\uff16\uff17\uff16X",
        "https://orc\u0131d.org/0000-0003-3001-676X",  # homoglyph host
    ],
)
async def test_get_author_by_orcid_rejects(
    client: AsyncClient, ingest_lsst_texmf: None, orcid: str
) -> None:
    """A value that is not an ORCID is a 422 located at the query parameter."""
    response = await client.get(f"/ook/authors?orcid={quote(orcid)}")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["query", "orcid"]


@pytest.mark.asyncio
async def test_authors_listing_unaffected(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """The bare listing and search modes are unchanged."""
    listing = await client.get("/ook/authors?limit=5")
    assert listing.status_code == 200
    assert len(listing.json()) == 5
    assert "X-Total-Count" in listing.headers

    search = await client.get("/ook/authors?search=Sick%2C%20Jonathan")
    assert search.status_code == 200
    assert any(r["internal_id"] == "sickj" for r in search.json())


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["limit=2", "search=Ho&limit=2"])
async def test_cursor_still_paginates_without_orcid(
    client: AsyncClient, ingest_lsst_texmf: None, query: str
) -> None:
    """Both paginated modes still walk their cursors, headers included."""
    first = await client.get(f"/ook/authors?{query}")
    assert first.status_code == 200
    assert "X-Total-Count" in first.headers

    next_url = PaginationLinkData.from_header(first.headers["Link"]).next_url
    assert next_url is not None
    assert "cursor=" in next_url

    second = await client.get(next_url)
    assert second.status_code == 200
    assert "X-Total-Count" in second.headers
    assert "Link" in second.headers
    first_ids = {entry["internal_id"] for entry in first.json()}
    second_ids = {entry["internal_id"] for entry in second.json()}
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.asyncio
async def test_get_author_by_orcid_conflicts_with_search(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """`orcid` and `search` together are a 422, not a silent name search."""
    response = await client.get(
        f"/ook/authors?orcid={SICKJ_ORCID}&search=Sick"
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["query", "orcid"]
    assert "orcid" in detail[0]["msg"]
    assert "search" in detail[0]["msg"]


@pytest.mark.asyncio
async def test_get_author_by_orcid_conflicts_with_cursor(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """`orcid` and `cursor` together are a 422: the lookup has no pages."""
    listing = await client.get("/ook/authors?limit=5")
    assert listing.status_code == 200
    cursor = PaginationLinkData.from_header(listing.headers["Link"]).next_url
    assert cursor is not None
    cursor = parse_qs(urlparse(cursor).query)["cursor"][0]

    response = await client.get(
        f"/ook/authors?orcid={SICKJ_ORCID}&cursor={quote(cursor)}"
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["query", "orcid"]
    assert "orcid" in detail[0]["msg"]
    assert "cursor" in detail[0]["msg"]


@pytest.mark.asyncio
async def test_get_author_by_orcid_emits_no_pagination(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """The ORCID response advertises no pagination machinery."""
    response = await client.get(f"/ook/authors?orcid={SICKJ_ORCID}")
    assert response.status_code == 200
    assert "Link" not in response.headers
    assert "X-Total-Count" not in response.headers


@pytest.mark.asyncio
async def test_get_author_by_orcid_ignores_limit(
    client: AsyncClient, ingest_lsst_texmf: None
) -> None:
    """`limit` cannot be told from absent, so it is accepted and inert."""
    narrow = await client.get(f"/ook/authors?orcid={SICKJ_ORCID}&limit=1")
    wide = await client.get(f"/ook/authors?orcid={SICKJ_ORCID}&limit=100")
    assert narrow.status_code == 200
    assert wide.status_code == 200
    assert narrow.json() == wide.json()
    assert len(narrow.json()) == 1
