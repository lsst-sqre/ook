"""Content reducer for Sphinx technotes hosted on LSST the Docs."""

from __future__ import annotations

import datetime
from typing import Any, Dict, List

import dateparser
import lxml.html

from ook.ingest.reducers.sphinxutils import (
    SphinxSection,
    clean_title_text,
    iter_sphinx_sections,
)

__all__ = ["ReducedLtdSphinxTechnote"]


class ReducedLtdSphinxTechnote:
    """A reduction of a Sphinx-based technical note, hosted on LSST the Docs,
    into sections.

    Parameters
    ----------
    html_source : `str`
        The source of the technote's HTML page.
    url : `str`
        The URL that the technote is hosted at. The URL does not need to
        include the ``index.html`` path since the canonical URL for a technote
        is just the directory; ``index.html`` will be stripped during the
        normalization process.
    metadata : `dict`
        Parsed contents of the ``metadata.yaml`` file found in the technote's
        GitHub repository.
    """

    def __init__(
        self, *, html_source: str, url: str, metadata: Dict[str, Any]
    ) -> None:
        self._html_source = html_source
        self.url = url
        self._metadata = metadata

        self._sections: List[SphinxSection] = []

        self._reduce_metadata()
        self._reduce_html()

    @property
    def url(self) -> str:
        """The URL of the content."""
        return self._url

    @url.setter
    def url(self, value: str) -> None:
        clip_string = "index.html"
        if value.endswith(clip_string):
            value = value[: -len(clip_string)]
        if not value.endswith("/"):
            value = f"{value}/"
        self._url = value

    @property
    def html_source(self) -> str:
        """The source of the technote's HTML page."""
        return self._html_source

    @property
    def h1(self) -> str:
        """The title of the page (also the title of the technote)."""
        return self._h1

    @property
    def sections(self) -> List[SphinxSection]:
        """The sections found in the technote."""
        return self._sections

    @property
    def description(self) -> str:
        """The description, or summary, of the document."""
        return self._description

    @property
    def series(self) -> str:
        """The technote series identifier."""
        return self._series

    @property
    def number(self) -> str:
        """The serial number of the technote within the series."""
        return self._number

    @property
    def handle(self) -> str:
        """The handle of the document."""
        return f"{self.series}-{self.number}"

    @property
    def author_names(self) -> List[str]:
        """Names of authors."""
        return self._authors

    @property
    def timestamp(self) -> datetime.datetime:
        """Timestamp associated with the technote (time when the document was
        updated).
        """
        return self._timestamp

    def _reduce_metadata(self) -> None:
        """Reduce the content of metadata.yaml."""
        try:
            self._h1: str = self._metadata["doc_title"]
        except KeyError:
            self._h1 = ""

        try:
            self._description: str = self._metadata["description"]
        except KeyError:
            self._description = ""

        try:
            self._series: str = self._metadata["series"]
        except KeyError:
            self._series = "UNKNOWN"

        try:
            self._number: str = self._metadata["serial_number"]
        except KeyError:
            self._number = "000"

        try:
            self._authors: List[str] = self._metadata["authors"]
        except KeyError:
            self._authors = []

    def _reduce_html(self) -> None:
        """Reduce the HTML document into sections."""
        doc = lxml.html.document_fromstring(self.html_source)

        self._timestamp = self._reduce_timestamp(doc)

        root_section = doc.cssselect(".document .section")[0]

        for s in iter_sphinx_sections(
            base_url=self._url,
            root_section=root_section,
            headers=[self.h1],
            header_callback=clean_title_text,
            content_callback=lambda x: x.strip(),
        ):
            self._sections.append(s)
        # Also look for additional h1 section on the page.
        # Technically, the page should only have one h1, and all content
        # should be subsections of that. In first-generation technotes, though,
        # h2 elements becamse h1 elements because the title got added
        # separately.
        for sibling in root_section.itersiblings(tag="div"):
            if "section" in sibling.classes:
                for s in iter_sphinx_sections(
                    root_section=sibling,
                    base_url=self._url,
                    headers=[self.h1],
                    header_callback=clean_title_text,
                    content_callback=lambda x: x.strip(),
                ):
                    self._sections.append(s)

    def _reduce_timestamp(
        self, doc: lxml.html.Html.Element
    ) -> datetime.datetime:
        try:
            date_element = doc.cssselect('a[href="#change-record"]')[0]
            date_text = date_element.text_content()
            return dateparser.parse(date_text, settings={"TIMEZONE": "UTC"})
        except IndexError as e:
            print(e)
            return datetime.datetime.utcnow()
