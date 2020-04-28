"""Content reducer for Lander-based PDF documents hosted on LSST the Docs."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import dateparser

from ook.classification import ContentType
from ook.ingest.reducers.utils import HANDLE_PATTERN, normalize_root_url

__all__ = ["ReducedLtdLanderDocument", "ContentChunk"]


class ReducedLtdLanderDocument:
    """A reduction of a Lander-based document, hosted on LSST the Docs.

    Lander-based sites have metadata.jsonld files that contain both metadata
    and a plain-text extraction of the document.
    """

    def __init__(self, *, url: str, metadata: Dict[str, Any]) -> None:
        self.url = url
        self.content_type = ContentType.LTD_LANDER_JSONLD
        self._metadata = metadata

        self._chunks: List[ContentChunk] = []

        self._reduce_metadata()

    @property
    def url(self) -> str:
        return self._url

    @url.setter
    def url(self, value: str) -> None:
        self._url = normalize_root_url(value)

    @property
    def h1(self) -> str:
        """The title of the document."""
        return self._h1

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

    @property
    def github_url(self) -> Optional[str]:
        """The URL of the technote's GitHub repository."""
        return self._github_url

    @property
    def chunks(self) -> List[ContentChunk]:
        return self._chunks

    def _reduce_metadata(self) -> None:
        """Reduce the content of a Lander-based document given its metadata
        and URL.

        ``self._metadata`` is a JSON-LD document in the
        codemeta.jsonld/schema.org format.
        """
        try:
            self._h1: str = self._metadata["name"].strip()
        except KeyError:
            self._h1 = ""

        try:
            self._authors: List[str] = [
                a["name"].strip() for a in self._metadata["author"]
            ]
        except KeyError:
            self._authors = []

        try:
            handle = self._metadata["reportNumber"]
            handle_match = HANDLE_PATTERN.match(handle)
            if handle_match:
                self._series: str = handle_match.group("series").upper()
                self._number: str = handle_match.group("number")
            else:
                raise ValueError
        except (KeyError, ValueError):
            self._series = ""
            self._number = ""

        try:
            self._timestamp: datetime.datetime = dateparser.parse(
                self._metadata["dateModified"], settings={"TIMEZONE": "UTC"}
            )
        except Exception:
            self._timestamp = datetime.datetime.utcnow()

        try:
            self._github_url: Optional[str] = self._metadata["codeRepository"]
        except KeyError:
            self._github_url = None

        try:
            self._description: str = self._metadata["description"].strip()
        except KeyError:
            self._description = ""

        if "articleBody" in self._metadata:
            self._segment_article_body(self._metadata["articleBody"])

    def _segment_article_body(self, body: str) -> None:
        """Segment an article into ContentChunks.

        This method assumes that the body has been prepared by Lander using
        pandoc. Pandoc adds newlines around headlines and makes those headlines
        into all-caps, giving us the ability to segment subsections.
        """
        sections = [s.strip() for s in body.split("\n\n\n\n") if s]
        for section in sections:
            self._process_section(section)

    def _process_section(self, section: str) -> None:
        parts = [p.strip() for p in section.split("\n\n") if p]
        heading = parts[0]
        for i, paragraph in enumerate(parts[1:]):
            chunk = ContentChunk(
                content=paragraph, headers=[self.h1, heading], paragraph=i
            )
            self._chunks.append(chunk)


@dataclass
class ContentChunk:
    """A chunk of content from a document (typically a paragraph)."""

    content: str
    """The plain-text content of the chunk."""

    headers: List[str]
    """The section headers, ordered by hierarchy.

    The header of the present section is the last element.
    """

    paragraph: int = 0
    """The paragraph sequence ID if there are multiple chunks with identical
    headers.
    """

    @property
    def header_level(self) -> int:
        """The header level of this section.

        For example, ``1`` corresponds to an "H1" section.
        """
        return len(self.headers)
