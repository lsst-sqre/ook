"""Utilities for content reducers."""

__all__ = ["normalize_root_url", "HANDLE_PATTERN"]

import re


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
