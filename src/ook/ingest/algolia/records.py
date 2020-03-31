"""Algolia search record types."""

from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from ook.ingest.reducers.ltdsphinxtechnote import ReducedLtdSphinxTechnote
    from ook.ingest.reducers.sphinxutils import SphinxSection

__all__ = ["LtdSphinxTechnoteSectionRecord"]


@dataclass
class LtdSphinxTechnoteSectionRecord:
    """An Algolia record for a section of a Sphinx-based technote.
    """

    section: SphinxSection
    """A section of content from the technote."""

    technote: ReducedLtdSphinxTechnote
    """The reduced technote."""

    surrogate_key: str
    """An unique identifier of an ingest for a given URL. Records with
    different surrogate keys must be "old" and therefore can be purged.
    """

    @property
    def object_id(self) -> str:
        """The objectID of the record.
        This is computed based on the URL and section heading hierarchy.
        """
        url_component = b64encode(
            self.section.url.lower().encode("utf-8")
        ).decode("utf-8")
        heading_component = b64encode(
            " ".join(self.section.headers).encode("utf-8")
        ).decode("utf-8")
        return f"{url_component}-{heading_component}"

    @property
    def data(self) -> Dict[str, Any]:
        """The JSON-encodable record, ready for indexing by Algolia."""
        record = {
            "objectID": self.object_id,
            "surrogateKey": self.surrogate_key,
            "url": self.section.url,
            "baseUrl": self.technote.url,
            "content": self.section.content,
            "importance": self.section.header_level,
            "contentType": "document",
            "handle": self.technote.handle,
            "number": self.technote.number,
            "series": self.technote.series,
            "authorNames": self.technote.author_names,
        }
        for i, header in enumerate(self.section.headers):
            record[f"h{i+1}"] = header
        return record
