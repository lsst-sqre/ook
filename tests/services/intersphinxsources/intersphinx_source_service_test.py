"""Tests for the IntersphinxSourceService's conflict reporting.

The store leaves a duplicate inventory URL as the database's own
`~sqlalchemy.exc.IntegrityError` so the service can report it in the API's
terms. These tests pin *which* integrity errors the service is willing to
speak for, and need no database to do it: the store is stubbed to raise the
error under test directly.
"""

from __future__ import annotations

import pytest
import structlog
from sqlalchemy.exc import IntegrityError

from ook.domain.intersphinxsources import IntersphinxSource
from ook.exceptions import ConflictError
from ook.services.intersphinxsources import (
    URL_UNIQUE_INDEX,
    IntersphinxSourceService,
)
from ook.storage.intersphinxsourcestore import IntersphinxSourceStore


class _RaisingSourceStore(IntersphinxSourceStore):
    """A store whose every write raises a prepared integrity error."""

    def __init__(self, error: IntegrityError) -> None:
        self._error = error

    async def add_source(
        self, *, url: str, title: str, enabled: bool = True
    ) -> IntersphinxSource:
        raise self._error


def _integrity_error(constraint: str) -> IntegrityError:
    """Return an integrity error naming ``constraint``, as psycopg does."""
    return IntegrityError(
        "INSERT INTO intersphinx_source ...",
        None,
        Exception(
            f'duplicate key value violates unique constraint "{constraint}"'
        ),
    )


def _service(error: IntegrityError) -> IntersphinxSourceService:
    """Return a service over a store that raises ``error`` on every write."""
    return IntersphinxSourceService(
        source_store=_RaisingSourceStore(error),
        logger=structlog.get_logger("test"),
    )


@pytest.mark.asyncio
async def test_duplicate_url_becomes_a_conflict() -> None:
    """The inventory URL's unique index is reported as a conflict."""
    service = _service(_integrity_error(URL_UNIQUE_INDEX))

    with pytest.raises(ConflictError) as caught:
        await service.register_source(
            url="https://pipelines.lsst.io/objects.inv", title="Pipelines"
        )

    assert "https://pipelines.lsst.io/objects.inv" in str(caught.value)


@pytest.mark.asyncio
async def test_other_integrity_errors_are_not_blamed_on_the_url() -> None:
    """An integrity error from anywhere else is left as the server-side
    failure it is.

    Reporting it as a 409 would tell an operator their URL was already
    registered when it was not, sending them to look for a registration
    that does not exist.
    """
    service = _service(_integrity_error("some_future_constraint"))

    with pytest.raises(IntegrityError):
        await service.register_source(
            url="https://pipelines.lsst.io/objects.inv", title="Pipelines"
        )
