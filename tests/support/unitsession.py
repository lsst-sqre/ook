"""The guard on infrastructure tests misclassified as unit tests.

``nox -s test-unit`` runs the part of the suite that needs neither the
PostgreSQL nor the Kafka testcontainer. What belongs to that part is decided
by the ``INFRA_TEST_PATHS`` ignore list in ``noxfile.py`` -- a hand-maintained
classification that lives nowhere near the tests it classifies. A new
database-backed test file that nobody adds to it passes locally under ``nox -s
test`` and then fails in CI's ``test-unit`` job with a raw
``DatabaseInitializationError('Connect call failed (127.0.0.1, 1)')``, ten
seconds deep in safir's retry loop, naming neither ``INFRA_TEST_PATHS`` nor
the session that applied it. Triaging that takes prior knowledge nobody has
the first time.

So the session marks itself with ``OOK_TEST_UNIT=1``, and the two ways a test
can reach for infrastructure check that marker first:

- requesting a fixture marked with `infrastructure_fixture`, caught by the
  ``pytest_runtest_setup`` hook in ``tests/conftest.py``, which pytest calls
  before it sets up any of the test's fixtures; and
- creating a database directly through
  `tests.support.database.provision_database`, which the tests that run DDL
  on their own database call instead of taking a fixture.

Both fail before anything connects, with a message that names the ignore list
to add the path to. The placeholder servers ``noxfile.py`` points the session
at remain as the backstop for a path neither route covers: they keep a
slipped-through test off real infrastructure, they are just slow and mute
about why.

Outside the unit session the whole guard is one environment lookup per test.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from typing import Any

import pytest

__all__ = [
    "UNIT_SESSION_ENV",
    "in_unit_session",
    "infrastructure_fixture",
    "infrastructure_fixtures",
    "reject_infrastructure_fixtures",
    "require_infrastructure",
]

UNIT_SESSION_ENV = "OOK_TEST_UNIT"
"""Environment variable with which ``nox -s test-unit`` marks itself.

``noxfile.py`` writes it in ``_unit_test_env``; ``tests/noxfile_test.py``
asserts that the name it writes is the one read here, so the two halves of
the marker cannot drift apart.
"""

_infrastructure_fixtures: set[str] = set()
"""Names of the fixtures `infrastructure_fixture` has marked."""


def infrastructure_fixture[FixtureT: Callable[..., Any]](
    fixture: FixtureT,
) -> FixtureT:
    """Mark a fixture as one that hands the test real infrastructure.

    Apply it *below* the ``@pytest.fixture`` (or ``@pytest_asyncio.fixture``)
    decorator, so pytest still registers the fixture function itself: this
    records the name and returns the function untouched.

    Marking is what puts a fixture on the guard's list, and the point of
    marking rather than listing names somewhere is that the list cannot drift
    away from the fixtures the way ``INFRA_TEST_PATHS`` drifts away from the
    tests. A new infrastructure fixture is guarded by the same line that
    declares it to be one.

    Only fixtures a test can name need marking; a fixture that merely depends
    on a marked one is covered already, because pytest resolves the whole
    closure into ``item.fixturenames``. Marking it anyway costs nothing and
    lets the failure name the fixture the test actually asked for.

    Parameters
    ----------
    fixture
        The undecorated fixture function.

    Returns
    -------
    Callable
        The same function, unchanged.
    """
    _infrastructure_fixtures.add(fixture.__name__)
    return fixture


def infrastructure_fixtures() -> frozenset[str]:
    """Return the names of every fixture marked as infrastructure.

    Returns
    -------
    frozenset of str
        Fixture names, as marked by `infrastructure_fixture` in whatever
        modules have been imported. ``tests/conftest.py`` defines them all and
        is imported before any test runs.
    """
    return frozenset(_infrastructure_fixtures)


def in_unit_session() -> bool:
    """Return whether this pytest process is the ``test-unit`` nox session.

    Returns
    -------
    bool
        `True` if `UNIT_SESSION_ENV` is set to a non-empty value, meaning no
        PostgreSQL or Kafka container is running.
    """
    return bool(os.environ.get(UNIT_SESSION_ENV))


def require_infrastructure(reason: str) -> None:
    """Fail the current test when the containers are not running.

    Parameters
    ----------
    reason
        Why this test needs the containers, phrased to follow "because" --
        for example ``"it requests app"``.

    Raises
    ------
    Failed
        Raised when the `UNIT_SESSION_ENV` marker is set. The failure carries
        no traceback: where the guard fired says nothing useful, while the
        message says everything.
    """
    if not in_unit_session():
        return
    pytest.fail(
        "This test needs the PostgreSQL and Kafka infrastructure, because"
        f" {reason}, and 'nox -s test-unit' starts neither. Add its path to"
        " INFRA_TEST_PATHS in noxfile.py, or run it with 'nox -s test'.",
        pytrace=False,
    )


def reject_infrastructure_fixtures(fixture_names: Iterable[str]) -> None:
    """Fail a test requesting an infrastructure fixture in the unit session.

    Called from the ``pytest_runtest_setup`` hook in ``tests/conftest.py``
    with the test's whole fixture closure, so it fires before pytest sets any
    of those fixtures up -- and therefore before the placeholder connection
    that would otherwise fail much later with nothing to point at.

    Parameters
    ----------
    fixture_names
        The test's fixture closure, ``item.fixturenames``.

    Raises
    ------
    Failed
        Raised, via `require_infrastructure`, when the test requests a marked
        fixture under the `UNIT_SESSION_ENV` marker.
    """
    if not in_unit_session():
        return
    requested = infrastructure_fixtures().intersection(fixture_names)
    if requested:
        require_infrastructure(f"it requests {', '.join(sorted(requested))}")
