"""Tests for the pytest-xdist worker isolation shim.

The shim in `tests.support.xdist` rewrites the environment so each xdist
worker gets its own PostgreSQL database and Kafka namespace, and it only
works if it runs before ``ook.config`` builds its module-level
``Configuration()`` singleton. These tests pin down both halves of that
contract: the import chain that keeps the shim reachable without dragging
``ook.config`` in, and the collection-time check that fails loudly when the
order is broken anyway.

They need no database or Kafka: the import guard runs in a subprocess that
imports and exits, and the verification tests call the checker directly with
the values a mis-ordered import would have left in the configuration.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ook.config import config

from .support.xdist import XdistIsolationError, verify_worker_isolation

_REPO_ROOT = Path(__file__).parents[1]

_DATABASE_SERVER = "postgresql+asyncpg://ook@127.0.0.1:5432"

_UNISOLATED_SETTINGS = {
    "database_url": f"{_DATABASE_SERVER}/ook",
    "ingest_kafka_topic": "ook.ingest",
    "linkcheck_kafka_topic": "ook.linkcheck",
    "kafka_consumer_group_id": "ook",
}
"""The settings ``ook.config`` holds when the shim never reached it.

These are the application defaults plus the base testcontainers database --
exactly what a ``Configuration()`` built before `isolate_xdist_worker` ran
would carry, and what every worker would then share.
"""


def _isolated_settings(worker_id: str) -> dict[str, str]:
    """Return the settings a correctly ordered import leaves for a worker."""
    return {
        "database_url": f"{_DATABASE_SERVER}/ook_{worker_id}",
        "ingest_kafka_topic": f"ook.ingest.{worker_id}",
        "linkcheck_kafka_topic": f"ook.linkcheck.{worker_id}",
        "kafka_consumer_group_id": f"ook-{worker_id}",
    }


_IMPORT_PROBE = """
import sys

import tests.support.xdist  # noqa: F401

print(",".join(sorted(m for m in sys.modules if m.startswith("ook."))))
"""


def test_shim_import_does_not_reach_ook_config() -> None:
    """Importing the shim must not import ``ook.config``.

    ``ook.config`` instantiates ``Configuration()`` at module import time,
    snapshotting the environment as it stands then. If any module the shim
    reaches imported it, the rewrite would land after the snapshot and every
    worker would silently share one database and one consumer group. This
    runs in a subprocess because the pytest process has ``ook.config``
    imported already, by way of this very conftest.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTEST_XDIST_WORKER"
    }
    env["PYTHONPATH"] = str(_REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"importing tests.support.xdist failed:\n{result.stderr}"
    )
    imported = [name for name in result.stdout.split(",") if name]
    assert "ook.config" not in imported, (
        "importing tests.support.xdist pulled in ook.config (via"
        f" {imported}), which snapshots the environment the shim is"
        " supposed to rewrite first"
    )


def test_verification_rejects_a_configuration_that_missed_the_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mis-ordered import fails collection instead of sharing a database.

    This simulates the outcome of importing ``ook.config`` ahead of the shim:
    the configuration keeps the base database, topics, and consumer group
    while the worker believes it is isolated. Without the check the run looks
    healthy and the four workers truncate each other's databases mid-test.
    """
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")

    with pytest.raises(XdistIsolationError) as excinfo:
        verify_worker_isolation(**_UNISOLATED_SETTINGS)

    message = str(excinfo.value)
    assert "gw0" in message
    # Every setting the shim owns is named, so the failure is diagnosable.
    assert "'ook'" in message
    assert "'ook.ingest.gw0'" in message
    assert "'ook.linkcheck.gw0'" in message
    assert "'ook-gw0'" in message
    # ... and so is the contract that was broken.
    assert "import-order contract" in message
    assert "isolate_xdist_worker" in message
    assert "ook.config" in message


def test_verification_accepts_an_isolated_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")

    verify_worker_isolation(**_isolated_settings("gw3"))


def test_verification_is_a_no_op_outside_xdist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A serial run has nothing to isolate, so base settings are correct."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)

    verify_worker_isolation(**_UNISOLATED_SETTINGS)


def test_this_worker_really_is_isolated() -> None:
    """The live configuration carries this worker's namespace.

    Everything else here exercises the checker with made-up settings, and the
    checker itself returns early when ``PYTEST_XDIST_WORKER`` is unset. This
    asserts against the real ``config``, so the parallel run the whole shim
    exists to protect cannot pass while quietly sharing one database.
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    if worker_id is None:
        pytest.skip("not running under pytest-xdist")

    verify_worker_isolation(
        database_url=str(config.database_url),
        ingest_kafka_topic=config.ingest_kafka_topic,
        linkcheck_kafka_topic=config.linkcheck_kafka_topic,
        kafka_consumer_group_id=config.kafka_consumer_group_id,
    )
    assert str(config.database_url).endswith(f"_{worker_id}")
