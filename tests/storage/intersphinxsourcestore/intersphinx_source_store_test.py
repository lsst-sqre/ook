"""Tests for the IntersphinxSourceStore."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ook.domain.intersphinxsources import SourceIngestStatus
from ook.factory import Factory


@pytest.mark.asyncio
async def test_add_source_roundtrip(factory: Factory) -> None:
    """A newly registered source round-trips with its observability
    columns unset.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()
        url = "https://pipelines.lsst.io/objects.inv"

        assert await store.get_source_by_url(url) is None

        source = await store.add_source(
            url=url, title="LSST Science Pipelines", enabled=True
        )

        assert source.url == url
        assert source.title == "LSST Science Pipelines"
        assert source.enabled is True
        assert source.date_ingested is None
        assert source.last_status is None
        assert source.last_error is None

        assert await store.get_source_by_url(url) == source
        assert await store.get_source(source.id) == source


@pytest.mark.asyncio
async def test_record_ingest_outcome(factory: Factory) -> None:
    """Recording an ingest failure round-trips the observability columns."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()
        source = await store.add_source(
            url="https://pipelines.lsst.io/objects.inv",
            title="LSST Science Pipelines",
        )
        now = datetime.now(tz=UTC).replace(microsecond=0)

        assert await store.record_ingest_outcome(
            source.id,
            date_ingested=now,
            status=SourceIngestStatus.failure,
            error="Connection timed out",
        )

        stamped = await store.get_source(source.id)
        assert stamped is not None
        assert stamped.date_ingested == now
        assert stamped.last_status is SourceIngestStatus.failure
        assert stamped.last_error == "Connection timed out"


@pytest.mark.asyncio
async def test_record_ingest_outcome_clears_error(factory: Factory) -> None:
    """A successful ingest clears the error a previous failure left."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()
        source = await store.add_source(
            url="https://pipelines.lsst.io/objects.inv",
            title="LSST Science Pipelines",
        )
        failed_at = datetime.now(tz=UTC).replace(microsecond=0)
        await store.record_ingest_outcome(
            source.id,
            date_ingested=failed_at,
            status=SourceIngestStatus.failure,
            error="Connection timed out",
        )

        await store.record_ingest_outcome(
            source.id,
            date_ingested=failed_at,
            status=SourceIngestStatus.success,
        )

        recovered = await store.get_source(source.id)
        assert recovered is not None
        assert recovered.last_status is SourceIngestStatus.success
        assert recovered.last_error is None


@pytest.mark.asyncio
async def test_record_ingest_outcome_unknown_source(factory: Factory) -> None:
    """Stamping an outcome onto an unregistered source reports no write."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()

        assert not await store.record_ingest_outcome(
            12345,
            date_ingested=datetime.now(tz=UTC),
            status=SourceIngestStatus.success,
        )


@pytest.mark.asyncio
async def test_update_source_leaves_omitted_fields(factory: Factory) -> None:
    """Disabling a source leaves its URL and title untouched."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()
        source = await store.add_source(
            url="https://pipelines.lsst.io/objects.inv",
            title="LSST Science Pipelines",
        )

        updated = await store.update_source(source.id, enabled=False)

        assert updated is not None
        assert updated.enabled is False
        assert updated.url == source.url
        assert updated.title == source.title


@pytest.mark.asyncio
async def test_update_source_unknown_id(factory: Factory) -> None:
    """Updating an unregistered source resolves to None."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()

        assert await store.update_source(12345, title="Nowhere") is None


@pytest.mark.asyncio
async def test_list_sources_enabled_only(factory: Factory) -> None:
    """The enabled-only listing omits parked sources."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()
        await store.add_source(
            url="https://pipelines.lsst.io/objects.inv",
            title="LSST Science Pipelines",
        )
        await store.add_source(
            url="https://example.org/objects.inv",
            title="Parked Site",
            enabled=False,
        )

        assert [source.title for source in await store.list_sources()] == [
            "Parked Site",
            "LSST Science Pipelines",
        ]
        assert [
            source.title
            for source in await store.list_sources(enabled_only=True)
        ] == ["LSST Science Pipelines"]


@pytest.mark.asyncio
async def test_delete_source(factory: Factory) -> None:
    """Deleting a source removes it, and deleting again reports no row."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()
        source = await store.add_source(
            url="https://pipelines.lsst.io/objects.inv",
            title="LSST Science Pipelines",
        )

        assert await store.delete_source(source.id)
        assert await store.get_source(source.id) is None
        assert not await store.delete_source(source.id)
