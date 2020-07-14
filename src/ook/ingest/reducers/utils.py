"""Utilities for content reducers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

__all__ = ["normalize_root_url", "HANDLE_PATTERN", "Handle"]


def normalize_root_url(url: str) -> str:
    """Normalize a root URL by removing any ``index.html`` path (if present)
    and ensuring that the path ends with ``/``.

    Parameters
    ----------
    url : `url`
        A URL.

    Returns
    -------
    str
        A URL.
    """
    clip_string = "index.html"
    if url.endswith(clip_string):
        url = url[: -len(clip_string)]
    if not url.endswith("/"):
        url = f"{url}/"
    return url


HANDLE_PATTERN = re.compile(r"^(?P<series>[a-zA-Z]+)-(?P<number>[0-9]+)$")
"""Regular expression pattern for a document handle.

For example, ``sqr-000`` or ``ldm-151``.
"""


@dataclass
class Handle:
    """A document handle."""

    series: str

    number: str

    @classmethod
    def parse(cls, handle: str) -> Handle:
        """Parse a handle in string form."""
        try:
            match = HANDLE_PATTERN.match(handle)
            if match is None:
                raise ValueError
        except Exception:
            raise ValueError(f"Could not parse handle: {handle}")

        return Handle(match["series"].upper(), match["number"])

    @classmethod
    def parse_from_subdomain(self, url: str) -> Handle:
        """Parse the handle from an LSST the Docs publication URL."""
        parts = urlparse(url)
        netloc = parts[1]
        subdomain = netloc.split(".")[0]
        return Handle.parse(subdomain)

    @property
    def handle(self) -> str:
        """The full handle."""
        return f"{self.series}-{self.number}"

    @property
    def number_as_int(self) -> int:
        try:
            return int(self.number)
        except ValueError:
            raise ValueError(f"Serial number is not an integer: {self.number}")
