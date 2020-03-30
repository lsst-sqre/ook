"""Support for classifying content sources."""

from __future__ import annotations

import enum
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import ClientSession

__all__ = [
    "DOC_SLUG_PATTERN",
    "ContentType",
    "classify_ltd_site",
    "is_document_handle",
    "has_jsonld_metadata",
]


DOC_SLUG_PATTERN = re.compile(r"^[a-z]+-[0-9]+$")
"""Regular expression pattern for a LTD product slug that matches a document.

For example, ``sqr-000`` or ``ldm-151``.
"""


class ContentType(enum.Enum):
    """Types of content that can be classified by Ook."""

    LTD_LANDER_JSONLD = "ltd_lander_jsonld"
    """A lander-based site for PDF-based content that includes a
    ``metadata.jsonld`` file and is hosted on LSST the Docs.
    """

    LTD_SPHINX_TECHNOTE = "ltd_sphinx_technote"
    """A Sphinx based technote that is hosted on LSST the Docs."""

    LTD_GENERIC = "ltd_generic"
    """A webpage that is hosted on LSST the Docs, but its precise content
    type is not known.
    """

    UNKNOWN = "unknown"
    """The content type is unclassified."""


async def classify_ltd_site(
    *, http_session: ClientSession, product_slug: str, published_url: str
) -> ContentType:
    """Classify the type of an LSST the Docs-based site.

    Parameters
    ----------
    http_session : `aiohttp.ClientSession`
        The HTTP client session.
    product_slug : `str`
        The LTD Product resource's slug.
    published_url : `str`
        The published URL of the site (usually the edition's published URL).

    Returns
    -------
    ContentType
        The known site type.
    """
    if is_document_handle(product_slug):
        # Either a lander-based site or a sphinx technote
        if await has_jsonld_metadata(
            http_session=http_session, published_url=published_url
        ):
            return ContentType.LTD_LANDER_JSONLD
        else:
            # NOTE this is slighly presumptive. If there are other types of
            # document we'll need to add more tests here.
            return ContentType.LTD_SPHINX_TECHNOTE
    else:
        return ContentType.LTD_GENERIC


def is_document_handle(product_slug: str) -> bool:
    """Test if a LSST the Docs product slug belongs to a Rubin Observatory
    document (as opposed to a general documentation site).

    Parameters
    ----------
    product_slug : `str`
        The "slug" of the LTD Product resource (which is the subdomain that
        the document is served from. For example, ``"sqr-000"`` is the slug
        for the https://sqr-001.lsst.io site of the SQR-000 technote.

    Returns
    -------
    bool
        `True` if the slug indicates a document or `False` otherwise.
    """
    if DOC_SLUG_PATTERN.match(product_slug):
        return True
    else:
        return False


async def has_jsonld_metadata(
    *, http_session: ClientSession, published_url: str
) -> bool:
    """Test if an LSST the Docs site has a ``metadata.jsonld`` path, indicating
    it is a Lander-based document.

    Parameters
    ----------
    http_session : `aiohttp.ClientSession`
        The HTTP client session.
    published_url : `str`
        The published URL of the site (usually the edition's published URL).

    Returns
    -------
    bool
        `True` if the ``metadata.jsonld`` path exists or `False` otherwise.
    """
    jsonld_name = "metadata.jsonld"
    if published_url.endswith("/"):
        jsonld_url = f"{published_url}{jsonld_name}"
    else:
        jsonld_url = f"{published_url}/{jsonld_name}"

    response = await http_session.head(jsonld_url)
    if response.status == 200:
        return True
    else:
        return False
