"""Tests for the ResourceStore document ingest path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from ook.dbschema.resources import SqlExternalReference, SqlResource
from ook.domain.base32id import (
    RESOURCE_ID_EPOCH,
    RESOURCE_ID_RANDOM_BITS,
    mint_resource_id_for_timestamp,
)
from ook.domain.resources import Document
from ook.factory import Factory


def _make_document(
    *,
    series: str = "Technical Notes",
    handle: str = "TEST-001",
    number: int = 1,
    title: str = "A Sample Technical Note",
    doi: str | None = None,
    external_relations: list[dict] | None = None,
) -> Document:
    """Build a minimal Document domain model for ingest.

    The ``id`` is a placeholder (0) because the storage layer resolves or
    mints the real ID; the timestamps are likewise overridden on write.
    """
    now = datetime.now(tz=UTC)
    return Document.model_validate(
        {
            "id": 0,
            "date_created": now,
            "date_updated": now,
            "title": title,
            "series": series,
            "handle": handle,
            "number": number,
            "doi": doi,
            "external_relations": external_relations or [],
        }
    )


def _external_relation(**external_reference: object) -> dict:
    """Build an ``external_relations`` entry citing an external reference.

    ``number_type`` is a required field on ``ExternalReference`` (no default),
    so it is defaulted to ``None`` here unless the caller overrides it.
    """
    return {
        "relation_type": "Cites",
        "external_reference": {"number_type": None, **external_reference},
    }


async def _resource_count(factory: Factory) -> int:
    """Return the total number of rows in the resource table."""
    result = await factory.db_session.execute(
        select(func.count()).select_from(SqlResource)
    )
    return result.scalar_one()


async def _external_reference_count(factory: Factory) -> int:
    """Return the total number of rows in the external_reference table."""
    result = await factory.db_session.execute(
        select(func.count()).select_from(SqlExternalReference)
    )
    return result.scalar_one()


@pytest.mark.asyncio
async def test_reingest_same_payload_is_idempotent(factory: Factory) -> None:
    """Re-ingesting the same payload keeps the ID stable and adds no rows."""
    async with factory.db_session.begin():
        store = factory.create_resource_store()

        first_id = await store.upsert_document(_make_document())
        assert await _resource_count(factory) == 1

        second_id = await store.upsert_document(_make_document())
        assert second_id == first_id
        assert await _resource_count(factory) == 1


@pytest.mark.asyncio
async def test_reingest_changed_title_updates_in_place(
    factory: Factory,
) -> None:
    """Re-ingesting with a new title updates the row under the same ID."""
    async with factory.db_session.begin():
        store = factory.create_resource_store()

        first_id = await store.upsert_document(
            _make_document(title="Original Title")
        )
        updated_id = await store.upsert_document(
            _make_document(title="Revised Title")
        )

        assert updated_id == first_id
        assert await _resource_count(factory) == 1

        resource = await store.get_resource_by_id(first_id)
        assert resource is not None
        assert resource.title == "Revised Title"


@pytest.mark.asyncio
async def test_reingest_resolves_by_doi(factory: Factory) -> None:
    """A matching DOI resolves to the existing row when the handle differs."""
    async with factory.db_session.begin():
        store = factory.create_resource_store()

        first_id = await store.upsert_document(
            _make_document(
                series="S", handle="H-1", number=1, doi="10.1000/shared"
            )
        )
        # Same DOI, different (series, handle) -> resolves by DOI.
        resolved_id = await store.upsert_document(
            _make_document(
                series="S",
                handle="H-2",
                number=2,
                doi="10.1000/shared",
                title="Renamed",
            )
        )

        assert resolved_id == first_id
        assert await _resource_count(factory) == 1

        resource = await store.get_resource_by_id(first_id)
        assert resource is not None
        assert isinstance(resource, Document)
        assert resource.handle == "H-2"
        assert resource.title == "Renamed"


@pytest.mark.asyncio
async def test_reingest_same_handle_changed_series_updates(
    factory: Factory,
) -> None:
    """Re-ingesting the same handle with a changed series updates in place.

    ``document_resource.handle`` is unique on its own, so a re-ingest that
    keeps the handle but changes the series must resolve to the existing row
    and update it rather than mint a new ID that collides on the handle.
    """
    async with factory.db_session.begin():
        store = factory.create_resource_store()

        first_id = await store.upsert_document(
            _make_document(series="Series A", handle="SAME-1", number=1)
        )
        updated_id = await store.upsert_document(
            _make_document(series="Series B", handle="SAME-1", number=1)
        )

        assert updated_id == first_id
        assert await _resource_count(factory) == 1

        resource = await store.get_resource_by_id(first_id)
        assert resource is not None
        assert isinstance(resource, Document)
        assert resource.series == "Series B"


@pytest.mark.asyncio
async def test_reingest_same_series_number_changed_handle_updates(
    factory: Factory,
) -> None:
    """Re-ingesting the same (series, number) with a new handle updates in
    place.

    ``UNIQUE (series, number)`` means a re-ingest that reuses an existing
    (series, number) under a different handle must resolve to the existing row
    and update it rather than mint a new ID that collides on (series, number).
    """
    async with factory.db_session.begin():
        store = factory.create_resource_store()

        first_id = await store.upsert_document(
            _make_document(series="Series X", handle="HANDLE-1", number=7)
        )
        updated_id = await store.upsert_document(
            _make_document(series="Series X", handle="HANDLE-2", number=7)
        )

        assert updated_id == first_id
        assert await _resource_count(factory) == 1

        resource = await store.get_resource_by_id(first_id)
        assert resource is not None
        assert isinstance(resource, Document)
        assert resource.handle == "HANDLE-2"


@pytest.mark.asyncio
async def test_new_document_mints_time_ordered_id(factory: Factory) -> None:
    """A new document mints a time-ordered ID in the storage layer."""
    before = datetime.now(tz=UTC)
    async with factory.db_session.begin():
        store = factory.create_resource_store()
        new_id = await store.upsert_document(_make_document())
    after = datetime.now(tz=UTC)

    # The high bits decode to milliseconds since the epoch; a randomly
    # generated ID would decode to a wildly different timestamp.
    milliseconds = new_id >> RESOURCE_ID_RANDOM_BITS
    minted_at = RESOURCE_ID_EPOCH + timedelta(milliseconds=milliseconds)
    assert before - timedelta(seconds=5) <= minted_at
    assert minted_at <= after + timedelta(seconds=5)


@pytest.mark.asyncio
async def test_documents_sort_in_creation_order(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Newly ingested documents sort in creation order under ID keyset."""
    # Force minted IDs to strictly increasing timestamps so creation order
    # is deterministic regardless of wall-clock resolution.
    timestamps = iter(
        RESOURCE_ID_EPOCH + timedelta(seconds=i) for i in (1, 2, 3)
    )
    monkeypatch.setattr(
        "ook.storage.resourcestore._store.generate_resource_id",
        lambda: mint_resource_id_for_timestamp(next(timestamps)),
    )

    async with factory.db_session.begin():
        store = factory.create_resource_store()
        ingested_ids = [
            await store.upsert_document(
                _make_document(
                    series="S",
                    handle=f"H-{i}",
                    number=i,
                    title=f"Doc {i}",
                )
            )
            for i in (1, 2, 3)
        ]
        assert ingested_ids == sorted(ingested_ids)

        listing = await store.get_resources()
        listed_ids = [entry.id for entry in listing.entries]
        assert listed_ids == ingested_ids


@pytest.mark.asyncio
async def test_id_collision_retries_with_fresh_id(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ID collision on insert retries instead of merging into a row."""
    async with factory.db_session.begin():
        store = factory.create_resource_store()

        first_id = await store.upsert_document(
            _make_document(series="S", handle="H-1", number=1, title="First")
        )

        # The next mint collides with the existing ID once, then succeeds
        # with a distinct ID (offset by one millisecond).
        fresh_id = first_id + (1 << RESOURCE_ID_RANDOM_BITS)
        minted = iter([first_id, fresh_id])
        monkeypatch.setattr(
            "ook.storage.resourcestore._store.generate_resource_id",
            lambda: next(minted),
        )

        second_id = await store.upsert_document(
            _make_document(series="S", handle="H-2", number=2, title="Second")
        )

        assert second_id == fresh_id
        assert second_id != first_id
        assert await _resource_count(factory) == 2

        # The first row must be untouched (not merged or overwritten).
        first = await store.get_resource_by_id(first_id)
        assert first is not None
        assert first.title == "First"
        assert isinstance(first, Document)
        assert first.handle == "H-1"


@pytest.mark.asyncio
async def test_ingest_timestamps_are_utc(factory: Factory) -> None:
    """Ingest stamps ``date_created`` and ``date_updated`` in UTC."""
    async with factory.db_session.begin():
        store = factory.create_resource_store()
        new_id = await store.upsert_document(_make_document())

        resource = await store.get_resource_by_id(new_id)
        assert resource is not None
        assert resource.date_created.utcoffset() == timedelta(0)
        assert resource.date_updated.utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_url_only_external_reference_dedupes(factory: Factory) -> None:
    """A DOI-less, URL-only external reference ingests and dedupes on URL.

    The ``ON CONFLICT (url)`` upsert path used to raise ``ProgrammingError``
    for want of a matching unique index. Two documents citing the same URL
    must resolve to a single ``external_reference`` row.
    """
    url = "https://example.org/paper"
    async with factory.db_session.begin():
        store = factory.create_resource_store()

        await store.upsert_document(
            _make_document(
                handle="TEST-001",
                number=1,
                external_relations=[_external_relation(url=url)],
            )
        )
        assert await _external_reference_count(factory) == 1

        # A second, distinct document citing the same URL dedupes on it.
        await store.upsert_document(
            _make_document(
                handle="TEST-002",
                number=2,
                external_relations=[_external_relation(url=url)],
            )
        )
        assert await _external_reference_count(factory) == 1


@pytest.mark.parametrize(
    ("key_field", "key_value"),
    [
        ("arxiv_id", "2401.00001"),
        ("isbn", "978-3-16-148410-0"),
        ("issn", "0000-0019"),
        ("ads_bibcode", "2024ApJ...900....1S"),
    ],
)
@pytest.mark.asyncio
async def test_identifier_only_external_reference_dedupes(
    factory: Factory, key_field: str, key_value: str
) -> None:
    """A reference keyed only by arXiv/ISBN/ISSN/bibcode dedupes on that key.

    Each identifier column carries its own unique constraint but the upsert
    used to insert without an arbiter, so a second citation raised
    ``IntegrityError`` instead of deduping. Two documents citing the same
    identifier-only reference must resolve to a single row.
    """
    async with factory.db_session.begin():
        store = factory.create_resource_store()

        await store.upsert_document(
            _make_document(
                handle="TEST-001",
                number=1,
                external_relations=[
                    _external_relation(**{key_field: key_value})
                ],
            )
        )
        assert await _external_reference_count(factory) == 1

        # A second, distinct document citing the same identifier dedupes.
        await store.upsert_document(
            _make_document(
                handle="TEST-002",
                number=2,
                external_relations=[
                    _external_relation(**{key_field: key_value})
                ],
            )
        )
        assert await _external_reference_count(factory) == 1


@pytest.mark.asyncio
async def test_identifier_only_external_reference_updates(
    factory: Factory,
) -> None:
    """Re-citing an identifier-only reference updates the existing row.

    The arbiter drives ``ON CONFLICT DO UPDATE``, so a second citation with a
    changed title updates the single row rather than erroring or duplicating.
    """
    async with factory.db_session.begin():
        store = factory.create_resource_store()

        await store.upsert_document(
            _make_document(
                handle="TEST-001",
                number=1,
                external_relations=[
                    _external_relation(
                        arxiv_id="2401.00001", title="Original Title"
                    )
                ],
            )
        )
        await store.upsert_document(
            _make_document(
                handle="TEST-002",
                number=2,
                external_relations=[
                    _external_relation(
                        arxiv_id="2401.00001", title="Revised Title"
                    )
                ],
            )
        )

        assert await _external_reference_count(factory) == 1
        title = (
            await factory.db_session.execute(
                select(SqlExternalReference.title).where(
                    SqlExternalReference.arxiv_id == "2401.00001"
                )
            )
        ).scalar_one()
        assert title == "Revised Title"


@pytest.mark.asyncio
async def test_keyless_external_reference_is_rejected(
    factory: Factory,
) -> None:
    """An external reference with no dedup key at all is rejected."""
    with pytest.raises(ValueError, match="identifier"):
        async with factory.db_session.begin():
            store = factory.create_resource_store()
            await store.upsert_document(
                _make_document(
                    external_relations=[_external_relation(title="No keys")]
                )
            )
