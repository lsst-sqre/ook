"""Tests for the guard on infrastructure tests misclassified as unit tests.

``nox -s test-unit`` decides what is a unit test from the ``INFRA_TEST_PATHS``
ignore list in ``noxfile.py``, which nothing keeps in step with the tests it
classifies. The guard in `tests.support.unitsession` turns the resulting
misclassification into an explanatory failure instead of a connection error
against the session's placeholder servers, and these tests pin down both the
marking that arms it and the failure it produces.

They need no database or Kafka: the guard's whole point is to fire before
anything connects, so exercising it is a matter of setting the session marker
and calling the check.
"""

from __future__ import annotations

import pytest

from . import conftest
from .support.database import provision_database
from .support.unitsession import UNIT_SESSION_ENV, infrastructure_fixtures


def test_the_infrastructure_fixtures_are_marked() -> None:
    """Every fixture that hands a test a container is on the guard's list.

    The list is built by the ``@infrastructure_fixture`` marks in
    ``tests/conftest.py`` rather than written out here, so this asserts
    against the real conftest: dropping a mark from one of these fixtures
    silently disarms the guard for every test that requests it.
    """
    assert infrastructure_fixtures() >= {
        "_app_lifespan",
        "_empty_database",
        "_rebuild_ddl_schema_after_test",
        "app",
        "client",
        "database_engine",
        "factory",
    }


def test_requesting_infrastructure_in_the_unit_session_is_refused(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A test that asks for the ``app`` fixture under the marker fails.

    The failure has to name the classification that put the test in the wrong
    session, because that is the one thing the connection error it replaces
    never mentioned.
    """
    monkeypatch.setenv(UNIT_SESSION_ENV, "1")
    node = request.node
    monkeypatch.setattr(node, "fixturenames", [*node.fixturenames, "app"])

    with pytest.raises(pytest.fail.Exception) as excinfo:
        conftest.pytest_runtest_setup(node)

    message = str(excinfo.value)
    assert "app" in message
    assert "INFRA_TEST_PATHS" in message
    assert "noxfile.py" in message
    assert "nox -s test" in message


def test_requesting_nothing_infrastructural_is_allowed(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuine unit test passes through the guard untouched.

    Exercised against this very test's own fixture closure, which is what
    every test in the ``test-unit`` session looks like.
    """
    monkeypatch.setenv(UNIT_SESSION_ENV, "1")

    conftest.pytest_runtest_setup(request.node)


def test_the_guard_is_inert_outside_the_unit_session(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``nox -s test`` starts the containers, so nothing is misclassified."""
    monkeypatch.delenv(UNIT_SESSION_ENV, raising=False)
    node = request.node
    monkeypatch.setattr(node, "fixturenames", [*node.fixturenames, "app"])

    conftest.pytest_runtest_setup(node)


@pytest.mark.asyncio
async def test_creating_a_database_in_the_unit_session_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tests that run DDL take no fixture, so the helper guards itself.

    ``tests/dbschema_test.py``, ``tests/migrations``, and the truncate tests
    reach for their own database through
    `tests.support.database.provision_database` rather than through a marked
    fixture, which puts them out of the hook's reach. Refusing here catches
    them before the ``CREATE DATABASE`` connection is attempted.
    """
    monkeypatch.setenv(UNIT_SESSION_ENV, "1")

    with pytest.raises(pytest.fail.Exception) as excinfo:
        await provision_database("ook_misclassified")

    assert "INFRA_TEST_PATHS" in str(excinfo.value)
