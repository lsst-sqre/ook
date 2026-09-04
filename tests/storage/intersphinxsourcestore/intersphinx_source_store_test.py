"""Tests for the IntersphinxSourceStore."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
import structlog
from safir.database import create_async_session, create_database_engine
from sqlalchemy.ext.asyncio import AsyncSession

from ook.config import config
from ook.domain.intersphinxsources import IntersphinxSource, SourceIngestStatus
from ook.factory import Factory
from ook.storage.intersphinxsourcestore import IntersphinxSourceStore

UNREGISTERED_SOURCE_ID = 12345
"""A primary key no test registers.

The store speaks integers -- the Base32 form is the API's business -- so
this is just an ID the registry does not hold.
"""

DIGEST = "a" * 64
"""A stand-in for the SHA-256 hex digest of an ingested inventory.

The store never hashes anything itself -- it records whatever the ingest
path hands it -- so a recognizable string says more here than a real
digest would.
"""

BLOCKED_GRACE = 0.5
"""How long a lock a test expects to be blocked is given to prove it.

The statement being waited on answers in milliseconds once it is unblocked
and its connection is already warm, so half a second is generous enough to
be reliable and short enough to keep the test cheap.
"""

LOCK_TIMEOUT = 30.0
"""How long a lock a test expects to be *granted* is waited on.

Only a bound on a hung test; a granted lock is granted at once.
"""


@asynccontextmanager
async def _second_session() -> AsyncIterator[AsyncSession]:
    """Open a second database session, on a connection of its own.

    Lock contention is only observable between two connections, and the
    ``factory`` fixture hands out one. This opens the other, on its own
    engine so neither session can be starved of the other's pool.
    """
    engine = create_database_engine(
        config.database_url, config.database_password
    )
    session = await create_async_session(engine)
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def _lock_in(
    session: AsyncSession, source_id: int
) -> IntersphinxSource | None:
    """Take, and immediately release, a source's row lock on one session."""
    store = IntersphinxSourceStore(
        session=session, logger=structlog.get_logger("test")
    )
    async with session.begin():
        return await store.lock_source(source_id)


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
async def test_record_ingest_outcome_stores_the_ingested_digest(
    factory: Factory,
) -> None:
    """A successful ingest records the digest of what it ingested."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()
        source = await store.add_source(
            url="https://pipelines.lsst.io/objects.inv",
            title="LSST Science Pipelines",
        )
        assert source.ingested_content_digest is None

        await store.record_ingest_outcome(
            source.id,
            date_ingested=datetime.now(tz=UTC),
            status=SourceIngestStatus.success,
            content_digest=DIGEST,
        )

        ingested = await store.get_source(source.id)
        assert ingested is not None
        assert ingested.ingested_content_digest == DIGEST


@pytest.mark.asyncio
async def test_record_ingest_outcome_keeps_the_digest_on_failure(
    factory: Factory,
) -> None:
    """A failed ingest leaves the digest the last success recorded.

    The links a failed ingest did not touch are still the ones that success
    wrote, so the digest describing them is still true. Clearing it would
    only buy a needless full re-ingest once the site comes back.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()
        source = await store.add_source(
            url="https://pipelines.lsst.io/objects.inv",
            title="LSST Science Pipelines",
        )
        await store.record_ingest_outcome(
            source.id,
            date_ingested=datetime.now(tz=UTC),
            status=SourceIngestStatus.success,
            content_digest=DIGEST,
        )

        await store.record_ingest_outcome(
            source.id,
            date_ingested=datetime.now(tz=UTC),
            status=SourceIngestStatus.failure,
            error="Connection timed out",
        )

        failed = await store.get_source(source.id)
        assert failed is not None
        assert failed.ingested_content_digest == DIGEST


@pytest.mark.asyncio
async def test_update_source_clears_the_digest_on_a_title_change(
    factory: Factory,
) -> None:
    """Retitling a source makes its ingested inventory worth re-reading.

    Every link the source contributed carries the title as its
    ``collection_title``, so the links no longer describe the registration
    even though the inventory behind them has not moved.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()
        source = await store.add_source(
            url="https://pipelines.lsst.io/objects.inv",
            title="LSST Science Pipelines",
        )
        await store.record_ingest_outcome(
            source.id,
            date_ingested=datetime.now(tz=UTC),
            status=SourceIngestStatus.success,
            content_digest=DIGEST,
        )

        updated = await store.update_source(source.id, title="Rubin Pipelines")

        assert updated is not None
        assert updated.ingested_content_digest is None


@pytest.mark.asyncio
async def test_update_source_clears_the_digest_on_a_url_change(
    factory: Factory,
) -> None:
    """Repointing a source at another inventory invalidates its digest.

    The links it holds were built from the old inventory, and the new one
    has to be read even if the two happen to hash alike.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()
        source = await store.add_source(
            url="https://pipelines.lsst.io/objects.inv",
            title="LSST Science Pipelines",
        )
        await store.record_ingest_outcome(
            source.id,
            date_ingested=datetime.now(tz=UTC),
            status=SourceIngestStatus.success,
            content_digest=DIGEST,
        )

        updated = await store.update_source(
            source.id, url="https://pipelines.lsst.io/v/weekly/objects.inv"
        )

        assert updated is not None
        assert updated.ingested_content_digest is None


@pytest.mark.asyncio
async def test_update_source_keeps_the_digest_when_nothing_links_change(
    factory: Factory,
) -> None:
    """Parking a source does not invalidate the links it already holds.

    ``enabled`` says nothing about what the links contain, and neither does
    a title or URL rewritten to the value it already had.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()
        source = await store.add_source(
            url="https://pipelines.lsst.io/objects.inv",
            title="LSST Science Pipelines",
        )
        await store.record_ingest_outcome(
            source.id,
            date_ingested=datetime.now(tz=UTC),
            status=SourceIngestStatus.success,
            content_digest=DIGEST,
        )

        updated = await store.update_source(
            source.id, enabled=False, title="LSST Science Pipelines"
        )

        assert updated is not None
        assert updated.ingested_content_digest == DIGEST


@pytest.mark.asyncio
async def test_record_ingest_outcome_unknown_source(factory: Factory) -> None:
    """Stamping an outcome onto an unregistered source reports no write."""
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()

        assert not await store.record_ingest_outcome(
            UNREGISTERED_SOURCE_ID,
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

        assert (
            await store.update_source(UNREGISTERED_SOURCE_ID, title="Nowhere")
            is None
        )


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


@pytest.mark.asyncio
async def test_lock_source_unknown_id(factory: Factory) -> None:
    """Locking an ID the registry does not hold reports no row.

    This is what a caller that raced a delete sees, and it must be
    distinguishable from a locked row rather than an error.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()
        assert await store.lock_source(UNREGISTERED_SOURCE_ID) is None


@pytest.mark.asyncio
async def test_lock_source_serializes_two_sessions(factory: Factory) -> None:
    """A second session's lock waits for the first, then reads what it wrote.

    Both halves matter to the ingest path this lock exists for: the waiting
    transaction must not proceed alongside the one ahead of it, and once it
    does proceed it must see that transaction's committed work rather than
    the row it read before it started waiting.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()
        source = await store.add_source(
            url="https://pipelines.lsst.io/objects.inv",
            title="Before",
        )

    async with _second_session() as other:
        # Warm the second connection before it is timed, so the wait below
        # measures lock contention rather than a Postgres handshake.
        async with other.begin():
            pass

        async with factory.db_session.begin():
            assert await store.lock_source(source.id) is not None
            waiter = asyncio.create_task(_lock_in(other, source.id))
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(
                    asyncio.shield(waiter), timeout=BLOCKED_GRACE
                )
            await store.update_source(source.id, title="After")

        locked = await asyncio.wait_for(waiter, timeout=LOCK_TIMEOUT)

    assert locked is not None
    assert locked.title == "After"


@pytest.mark.asyncio
async def test_lock_source_does_not_block_another_source(
    factory: Factory,
) -> None:
    """One source's lock leaves every other source's free.

    The registration row is the lock key precisely so that ingests of
    different sites do not queue behind each other.
    """
    async with factory.db_session.begin():
        store = factory.create_intersphinx_source_store()
        locked_source = await store.add_source(
            url="https://a.example/objects.inv", title="A docs"
        )
        other_source = await store.add_source(
            url="https://b.example/objects.inv", title="B docs"
        )

    async with _second_session() as other:
        async with factory.db_session.begin():
            assert await store.lock_source(locked_source.id) is not None
            free = await asyncio.wait_for(
                _lock_in(other, other_source.id), timeout=LOCK_TIMEOUT
            )

    assert free is not None
    assert free.title == "B docs"
