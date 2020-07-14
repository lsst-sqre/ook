"""Content reducer for Lander-based PDF documents hosted on LSST the Docs."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import dateparser

from ook.classification import ContentType
from ook.ingest.reducers.utils import Handle, normalize_root_url

if TYPE_CHECKING:
    from structlog._config import BoundLoggerLazyProxy

__all__ = ["ReducedLtdLanderDocument", "ContentChunk"]


class ReducedLtdLanderDocument:
    """A reduction of a Lander-based document, hosted on LSST the Docs.

    Lander-based sites have metadata.jsonld files that contain both metadata
    and a plain-text extraction of the document.
    """

    def __init__(
        self,
        *,
        url: str,
        metadata: Dict[str, Any],
        logger: BoundLoggerLazyProxy,
    ) -> None:
        self.url = url
        self.content_type = ContentType.LTD_LANDER_JSONLD
        self._metadata = metadata
        self._logger = logger

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
    def number(self) -> Optional[int]:
        """The serial number of the technote within the series."""
        return self._number

    @property
    def handle(self) -> str:
        """The handle of the document."""
        return self._handle

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
            handle = Handle.parse(self._metadata["reportNumber"])
        except (ValueError, KeyError):
            # Fall back to getting handle from ingest URL
            handle = Handle.parse_from_subdomain(self.url)

        self._handle: str = handle.handle
        self._series: str = handle.series
        self._number: int = handle.number_as_int

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

        if "articleBody" in self._metadata:
            self._segment_article_body(self._metadata["articleBody"])
        else:
            self._logger.debug("No article body", handle=self._handle)

        try:
            self._description: str = self._metadata["description"].strip()
        except (KeyError, AttributeError):
            # Fallback to using the first content chunk as the description
            try:
                self._description = self._chunks[0].content
            except IndexError:
                self._description = ""

        if len(self._chunks) == 0:
            # Many new documents don't have any content, but they usually
            # do have an abstract. This creates a fake initial record.
            self._chunks.append(
                ContentChunk(
                    content=self._description, headers=[self.h1], paragraph=1
                )
            )

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
        heading = parts[0].strip()
        if heading.startswith("\\"):
            # simple heuristic to avoid pandoc conversion failures
            return

        for i, paragraph in enumerate(parts[1:]):
            if paragraph.startswith("\\"):
                # simple heuristic to avoid pandoc conversion failures
                continue

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
