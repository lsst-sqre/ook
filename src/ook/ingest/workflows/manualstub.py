"""Workfor for manually adding a document stub record to Algolia, without
parsing a document.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import structlog
from algoliasearch.search_index_async import SearchIndexAsync
from pydantic import BaseModel, Field, HttpUrl

from ook.classification import ContentType
from ook.ingest.algolia.expiration import delete_old_records
from ook.ingest.algolia.records import (
    DocumentRecord,
    format_timestamp,
    format_utc_datetime,
    generate_object_id,
    generate_surrogate_key,
)


class MinimalDocumentModel(BaseModel):
    """Model for a manually-added record."""

    title: str = Field(description="Document's title")

    handle: str = Field(description="Document handle.")

    url: HttpUrl = Field(description="The document's URL.")

    authorNames: list[str] = Field(
        default_factory=list, description="Author names"
    )

    description: str = Field(description="Description of the document.")

    githubRepoUrl: Optional[HttpUrl] = Field(
        None, description="URL of the source repository."
    )

    def make_algolia_record(self) -> DocumentRecord:
        object_id = generate_object_id(url=str(self.url), headers=[self.title])
        surrogate_key = generate_surrogate_key()
        now = datetime.utcnow().replace(tzinfo=timezone.utc)
        series, _number = self.handle.split("-")

        return DocumentRecord(
            objectID=object_id,
            baseUrl=self.url,
            url=self.url,
            surrogateKey=surrogate_key,
            sourceUpdateTime=format_utc_datetime(now),
            sourceUpdateTimestamp=format_timestamp(now),
            sourceCreationTimestamp=None,
            recordUpdateTime=format_utc_datetime(now),
            contentCategories_lvl0="Documents",
            contentCategories_lvl1=f"Documents > {series.upper()}",
            contentType=ContentType.UNKNOWN.value,
            description=self.description,
            content=self.description,
            handle=self.handle,
            number=int(_number),
            series=series,
            authorNames=self.authorNames,
            pIndex=None,
            h1=self.title,
            githubRepoUrl=self.githubRepoUrl,
        )


async def add_manual_doc_stub(
    algolia_index: SearchIndexAsync, dataset: str
) -> None:
    logger = structlog.get_logger(__file__)
    data = MinimalDocumentModel.parse_raw(dataset)
    record = data.make_algolia_record()
    record_dict = record.dict(by_alias=True, exclude_none=True)
    print(record_dict)
    result = await algolia_index.save_objects_async([record_dict])
    print(result)
    await delete_old_records(
        index=algolia_index,
        base_url=record.baseUrl,
        surrogate_key=record.surrogateKey,
        logger=logger,
    )
