"""Tests for the Algolia document index service.

Algolia applies writes asynchronously and in order: a save returns as soon
as the task is queued, so a browse issued right after it can still see the
previous records. These tests drive the service against a fake index that
reproduces that lag so the replace logic is exercised under the conditions
that made re-ingested documents vanish from the production index.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest
import structlog

from ook.domain.algoliarecord import (
    DocumentRecord,
    DocumentSourceType,
    MinimalDocumentModel,
    format_timestamp,
    format_utc_datetime,
)
from ook.services.algoliadocindex import AlgoliaDocIndexService

_FILTER_PATTERN = re.compile(
    r'baseUrl:"(?P<base_url>[^"]+)"'
    r'(?: AND NOT surrogateKey:"(?P<excluded_key>[^"]+)")?'
)


class LaggingIndex:
    """A fake Algolia index whose writes only become visible after `flush`.

    Saves and deletes are queued in call order, exactly as Algolia's
    indexing queue applies them, while `browse_objects` answers from the
    last flushed state. That is the window in which the production service
    deleted the records it had just saved.
    """

    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self.records: dict[str, dict[str, Any]] = {
            record["objectID"]: dict(record) for record in records or []
        }
        self.pending: list[tuple[str, list[Any]]] = []
        self.delete_calls: list[list[str]] = []

    async def save_objects_async(self, objects: list[dict[str, Any]]) -> None:
        self.pending.append(("save", [dict(obj) for obj in objects]))

    async def delete_objects_async(self, object_ids: list[str]) -> None:
        self.delete_calls.append(list(object_ids))
        self.pending.append(("delete", list(object_ids)))

    def browse_objects(self, settings: dict[str, Any]) -> list[dict[str, Any]]:
        match = _FILTER_PATTERN.fullmatch(settings["filters"])
        assert match is not None, settings["filters"]
        base_url = match.group("base_url")
        excluded_key = match.group("excluded_key")
        return [
            dict(record)
            for record in self.records.values()
            if str(record["baseUrl"]) == base_url
            and record.get("surrogateKey") != excluded_key
        ]

    def flush(self) -> None:
        """Apply the queued writes in order."""
        for operation, payload in self.pending:
            if operation == "save":
                for obj in payload:
                    self.records[obj["objectID"]] = obj
            else:
                for object_id in payload:
                    self.records.pop(object_id, None)
        self.pending.clear()

    def surrogate_keys(self, base_url: str) -> set[str]:
        """Return the surrogate keys of the flushed records for a URL."""
        return {
            record["surrogateKey"]
            for record in self.records.values()
            if str(record["baseUrl"]) == base_url
        }


def make_record(
    base_url: str, section: str | None, surrogate_key: str
) -> DocumentRecord:
    """Build a document record the way the technote ingests do."""
    now = datetime.now(tz=UTC)
    url = f"{base_url}#{section}" if section else base_url
    headers = ["Title", section] if section else ["Title"]
    return DocumentRecord(
        object_id=DocumentRecord.generate_object_id(url=url, headers=headers),
        base_url=base_url,  # type: ignore[arg-type]
        url=url,
        surrogate_key=surrogate_key,
        source_update_time=format_utc_datetime(now),
        source_update_timestamp=format_timestamp(now),
        source_creation_timestamp=None,
        record_update_time=format_utc_datetime(now),
        content_categories_lvl0="Documents",
        content_categories_lvl1="Documents > SQR",
        content_type=DocumentSourceType.LTD_TECHNOTE,
        description="Description",
        content=f"Content of {section or 'the document'}",
        handle="SQR-000",
        number=0,
        series="SQR",
        author_names=["Example Author"],
        p_index=None,
        h1="Title",
        h2=section,
        github_repo_url=None,
    )


def exported(records: list[DocumentRecord]) -> list[dict[str, Any]]:
    return [record.export_for_algolia() for record in records]


def make_service(index: LaggingIndex) -> AlgoliaDocIndexService:
    return AlgoliaDocIndexService(
        index,
        structlog.get_logger("ook"),
    )


BASE_URL = "https://sqr-000.lsst.io/"
OTHER_URL = "https://sqr-001.lsst.io/"


@pytest.mark.asyncio
async def test_reingest_survives_indexing_lag() -> None:
    """Re-ingesting a document keeps its records even when the browse that
    decides what to delete still sees the previous ingest.
    """
    sections = ["intro", "method", "results"]
    index = LaggingIndex(
        exported([make_record(BASE_URL, s, "old-key") for s in sections])
    )
    service = make_service(index)

    new_records = [make_record(BASE_URL, s, "new-key") for s in sections]
    await service.save_document_records(new_records)
    index.flush()

    assert len(index.records) == 3
    assert index.surrogate_keys(BASE_URL) == {"new-key"}
    assert index.delete_calls == []


@pytest.mark.asyncio
async def test_removed_sections_are_deleted() -> None:
    """Records whose object IDs are absent from the new ingest are removed,
    while other documents are untouched.
    """
    index = LaggingIndex(
        exported(
            [
                make_record(BASE_URL, "intro", "old-key"),
                make_record(BASE_URL, "dropped", "old-key"),
                make_record(OTHER_URL, "intro", "other-key"),
            ]
        )
    )
    service = make_service(index)

    await service.save_document_records(
        [
            make_record(BASE_URL, "intro", "new-key"),
            make_record(BASE_URL, "added", "new-key"),
        ]
    )
    index.flush()

    kept = {
        record["url"]
        for record in index.records.values()
        if str(record["baseUrl"]) == BASE_URL
    }
    assert kept == {f"{BASE_URL}#intro", f"{BASE_URL}#added"}
    assert index.surrogate_keys(BASE_URL) == {"new-key"}
    assert index.surrogate_keys(OTHER_URL) == {"other-key"}
    assert len(index.delete_calls) == 1


@pytest.mark.asyncio
async def test_first_ingest_issues_no_delete() -> None:
    index = LaggingIndex()
    service = make_service(index)

    await service.save_document_records(
        [make_record(BASE_URL, s, "key") for s in ("intro", "method")]
    )
    index.flush()

    assert len(index.records) == 2
    assert index.delete_calls == []


@pytest.mark.asyncio
async def test_records_from_several_documents_are_replaced_per_url() -> None:
    index = LaggingIndex(
        exported(
            [
                make_record(BASE_URL, "stale", "old-a"),
                make_record(OTHER_URL, "stale", "old-b"),
            ]
        )
    )
    service = make_service(index)

    await service.save_document_records(
        [
            make_record(BASE_URL, "intro", "new-a"),
            make_record(OTHER_URL, "intro", "new-b"),
        ]
    )
    index.flush()

    assert index.surrogate_keys(BASE_URL) == {"new-a"}
    assert index.surrogate_keys(OTHER_URL) == {"new-b"}
    assert len(index.records) == 2


@pytest.mark.asyncio
async def test_mixed_surrogate_keys_for_one_url_are_rejected() -> None:
    index = LaggingIndex()
    service = make_service(index)

    with pytest.raises(RuntimeError, match="Multiple surrogate keys"):
        await service.save_document_records(
            [
                make_record(BASE_URL, "intro", "key-a"),
                make_record(BASE_URL, "method", "key-b"),
            ]
        )
    assert index.pending == []


@pytest.mark.asyncio
async def test_doc_stub_replaces_previous_stub_under_lag() -> None:
    stub = MinimalDocumentModel(
        title="A stub document",
        handle="LDM-999",
        url="https://ldm-999.lsst.io/",  # type: ignore[arg-type]
        description="Stub description",
    )
    previous = stub.make_algolia_record().export_for_algolia()
    index = LaggingIndex([previous])
    service = make_service(index)

    await service.save_doc_stub(stub)
    index.flush()

    assert len(index.records) == 1
    (record,) = index.records.values()
    assert record["objectID"] == previous["objectID"]
    assert record["surrogateKey"] != previous["surrogateKey"]
    assert index.delete_calls == []
