"""Utilities for content reducers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Self
from urllib.parse import urlparse

__all__ = ["HANDLE_PATTERN", "Handle", "normalize_root_url"]


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
    url = url.removesuffix(clip_string)
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
    def parse(cls, handle: str) -> Self:
        """Parse a handle in string form."""
        try:
            match = HANDLE_PATTERN.match(handle)
            if match is None:
                raise ValueError(f"Could not parse handle: {handle}")
        except Exception as e:
            raise ValueError(f"Could not parse handle: {handle}") from e

        return cls(match["series"].upper(), match["number"])

    @classmethod
    def parse_from_subdomain(cls, url: str) -> Self:
        """Parse the handle from an LSST the Docs publication URL."""
        parts = urlparse(url)
        netloc = parts[1]
        subdomain = netloc.split(".")[0]
        return cls.parse(subdomain)

    @property
    def handle(self) -> str:
        """The full handle."""
        return f"{self.series}-{self.number}"

    @property
    def number_as_int(self) -> int:
        try:
            return int(self.number)
        except ValueError as e:
            raise ValueError(
                f"Serial number is not an integer: {self.number}"
            ) from e
