"""Domain model for a technote (technote.lsst.io) document."""

from __future__ import annotations

from datetime import UTC, datetime

import lxml.html

from ..exceptions import DocumentParsingError
from .algoliarecord import (
    DocumentRecord,
    DocumentSourceType,
    format_timestamp,
    format_utc_datetime,
    generate_surrogate_key,
)
from .sphinxutils import SphinxSection, clean_title_text, iter_sphinx_sections

__all__ = ["LtdTechnote"]


class LtdTechnote:
    """A representation of an LTD-deployed technote (technote.lsst.io)
    document.
    """

    def __init__(self, html: str) -> None:
        self._html = lxml.html.document_fromstring(html)

    @property
    def content_type(self) -> DocumentSourceType:
        """The document's content type."""
        return DocumentSourceType.LTD_TECHNOTE

    @property
    def url(self) -> str:
        """The URL of the technote."""
        try:
            return self._html.cssselect("link[rel='canonical']")[-1].get(
                "href"
            )
        except Exception as e:
            raise DocumentParsingError(
                "Unable to parse technote canonical URL"
            ) from e

    @property
    def h1(self) -> str:
        """The document's title."""
        try:
            return self._html.cssselect("title")[0].text_content()
        except Exception as e:
            raise DocumentParsingError("Unable to parse technote title") from e

    @property
    def description(self) -> str:
        """The document's description or abstract."""
        key = "description"
        try:
            return self._html.cssselect(f"meta[name='{key}']")[-1].get(
                "content"
            )
        except Exception as e:
            raise DocumentParsingError(f"Unable to parse {key}") from e

    @property
    def update_time(self) -> datetime:
        """The document's update time."""
        key = "og:article:modified_time"
        try:
            date_text = self._html.cssselect(f"meta[property='{key}']")[
                -1
            ].get("content")
        except Exception:
            # The modified time may not be available. Fall back "now" on
            # the assumption the document ingest is triggered by a
            # documentation update
            return datetime.now(tz=UTC)
        try:
            dt = datetime.fromisoformat(date_text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
        except Exception as e:
            raise DocumentParsingError(f"Unable to parse {key}") from e
        return dt

    @property
    def creation_time(self) -> datetime | None:
        """The document's original creation time."""
        key = "og:article:published_time"
        try:
            dt = datetime.fromisoformat(
                self._html.cssselect(f"meta[property='{key}']")[-1].get(
                    "content"
                )
            )
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
        except Exception as e:
            raise DocumentParsingError(f"Unable to parse {key}") from e
        return dt

    @property
    def handle(self) -> str:
        """The document's handle."""
        key = "citation_technical_report_number"
        try:
            return self._html.cssselect(f"meta[name='{key}']")[-1].get(
                "content"
            )
        except Exception as e:
            raise DocumentParsingError(f"Unable to parse {key}") from e

    @property
    def series(self) -> str:
        """The document's series."""
        return self.handle.split("-")[0]

    @property
    def number(self) -> int:
        """The document's number."""
        return int(self.handle.split("-")[1])

    @property
    def source_repository_url(self) -> str | None:
        """The URL of the document's source repository."""
        key = "data-technote-source-url"
        try:
            source_link = self._html.cssselect(f"[{key}]")[-1]
        except IndexError:
            return None
        return source_link.get(key)

    @property
    def author_names(self) -> list[str]:
        """The document's authors' names."""
        key = "citation_author"
        return [
            t.get("content")
            for t in self._html.cssselect(f"meta[name='{key}']")
        ]

    @property
    def sections(self) -> list[SphinxSection]:
        """The sections of the document (content between header tags)."""
        try:
            first_section = self._html.cssselect("article.e-content section")[
                0
            ]
        except IndexError as e:
            raise DocumentParsingError(
                "Unable to find the article tag in the technote."
            ) from e

        return list(
            iter_sphinx_sections(
                base_url=self.url,
                root_section=first_section,
                headers=[],
                header_callback=clean_title_text,
            )
        )

    def make_algolia_records(self) -> list[DocumentRecord]:
        """Return the Algolia document records for this technote."""
        surrogate_key = generate_surrogate_key()

        records = [
            self._create_record(section=s, surrogate_key=surrogate_key)
            for s in self.sections
        ]

        # The top-level section typically has no content because it is
        # immediately followed by the Abstract. Set the description to
        # that top-level section because this is what will be browsed by
        # default in Algolia.
        if len(records[-1].content) == 0 and records[-1].h2 is None:
            records[-1].content = self.description

        return records

    def _create_record(
        self, *, section: SphinxSection, surrogate_key: str
    ) -> DocumentRecord:
        """Create an Algolia DocumentRecord for a section."""
        object_id = DocumentRecord.generate_object_id(
            url=section.url, headers=section.headers
        )

        record_args = {
            "object_id": object_id,
            "surrogate_key": surrogate_key,
            "source_update_time": format_utc_datetime(self.update_time),
            "source_update_timestamp": format_timestamp(self.update_time),
            "source_creation_timestamp": (
                format_timestamp(self.creation_time)
                if self.creation_time
                else None
            ),
            "record_update_time": format_utc_datetime(datetime.now(tz=UTC)),
            "url": section.url,
            "base_url": self.url,
            "content": section.content,
            "importance": section.header_level,
            "content_categories_lvl0": "Documents",
            "content_categories_lvl1": (f"Documents > {self.series.upper()}"),
            "content_type": self.content_type.value,
            "description": self.description,
            "handle": self.handle,
            "number": self.number,
            "series": self.series,
            "author_names": self.author_names,
            "github_repo_url": self.source_repository_url,
        }

        for i, header in enumerate(section.headers):
            record_args[f"h{i+1}"] = header

        return DocumentRecord.parse_obj(record_args)
