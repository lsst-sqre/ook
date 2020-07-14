"""Tests for the ook.ingest.reducers.utils module."""

import pytest

from ook.ingest.reducers.utils import Handle


def test_handle() -> None:
    handle = Handle(series="SQR", number="000")
    assert handle.series == "SQR"
    assert handle.number == "000"
    assert handle.handle == "SQR-000"
    assert handle.number_as_int == 0


@pytest.mark.parametrize(
    "handle, expected_series, expected_number",
    [("SQR-000", "SQR", "000"), ("sqr-000", "SQR", "000")],
)
def test_handle_parse(
    handle: str, expected_series: str, expected_number: str
) -> None:
    h = Handle.parse(handle)
    assert h.series == expected_series
    assert h.number == expected_number


@pytest.mark.parametrize(
    "url, expected_series, expected_number",
    [
        ("https://sqr-000.lsst.io", "SQR", "000"),
        ("https://dmtn-145.lsst.io/", "DMTN", "145"),
    ],
)
def test_handle_url_parse(
    url: str, expected_series: str, expected_number: str
) -> None:
    h = Handle.parse_from_subdomain(url)
    assert h.series == expected_series
    assert h.number == expected_number


def test_handle_exceptions() -> None:
    """Exercise different modes for generating a ValueError from parsing a
    handle.
    """
    h1 = Handle(series="TS", number="TMA")
    with pytest.raises(ValueError):
        h1.number_as_int

    with pytest.raises(ValueError):
        Handle.parse("TS-TMA")

    with pytest.raises(ValueError):
        Handle.parse_from_subdomain("https://ts_tma.lsst.io/")
