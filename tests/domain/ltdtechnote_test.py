"""Test for ook.domain.ltdtechnote."""

from __future__ import annotations

from pathlib import Path

from ook.domain.ltdtechnote import LtdTechnote


def test_sqr_075() -> None:
    """Test parsing SQR-075 into a LtdTechnote domain object."""
    root_path = Path(__file__).parent.parent / "data" / "content" / "sqr-075"
    html_path = root_path / "html" / "index.html"

    tn = LtdTechnote(html_path.read_text())
    records = tn.make_algolia_records()
    assert len(records) > 1
    for record in records:
        print(record.headers)
        print(f"\t{record.content[:50]}...")

    # Last section corresponds to the H1 title and uses the abstract as content
    assert records[-1].content.startswith("The SQuaRE team has")
    assert records[-1].h2 is None
    assert records[-1].content == records[-1].description

    # First section corresponds to the abstract section
    assert records[0].content.startswith("The SQuaRE team has")
    assert records[0].h2 == "Abstract"

    assert records[1].h2 == "Problem statement"
    assert records[1].author_names == ["Jonathan Sick"]
    assert records[1].base_url == "https://sqr-075.lsst.io/"
    assert records[1].url == "https://sqr-075.lsst.io/#problem-statement"
    assert records[1].handle == "SQR-075"
    assert records[1].number == 75
    assert records[1].series == "SQR"
    assert records[1].github_repo_url == "https://github.com/lsst-sqre/sqr-075"
