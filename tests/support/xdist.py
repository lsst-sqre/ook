"""Per-worker isolation for pytest-xdist runs.

**Nothing this module imports may reach ``ook.config``, at any depth.**
``ook.config`` builds its ``config = Configuration()`` singleton at module
import time, snapshotting the environment exactly as it stands then. The
whole point of `isolate_xdist_worker` is to rewrite that environment first,
so if importing this module dragged ``ook.config`` in, the rewrite would
land after the snapshot: every worker would keep the base database, topics,
and consumer group, and the per-test truncate in one worker would clobber
the tests running in the other three -- silently, because everything still
"works". The only application module reachable from here is ``ook.dbschema``
(by way of `tests.support.database`), which imports SQLAlchemy and nothing
else of the application. Keep it that way; ``tests/xdist_isolation_test.py``
guards the chain, and `verify_worker_isolation` catches a broken import
order at collection time.
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit

from .database import provision_database

__all__ = [
    "XdistIsolationError",
    "isolate_xdist_worker",
    "verify_worker_isolation",
]

_WORKER_KAFKA_SETTINGS = {
    "ingest_kafka_topic": (
        "OOK_INGEST_KAFKA_TOPIC",
        "ook.ingest.{worker_id}",
    ),
    "linkcheck_kafka_topic": (
        "OOK_LINKCHECK_KAFKA_TOPIC",
        "ook.linkcheck.{worker_id}",
    ),
    "kafka_consumer_group_id": ("OOK_GROUP_ID", "ook-{worker_id}"),
}
"""Per-worker Kafka settings, keyed by `ook.config.Configuration` attribute.

Each value pairs the environment variable `isolate_xdist_worker` rewrites with
the name template `verify_worker_isolation` expects to find in the resulting
configuration, so the two cannot drift apart.
"""

_IMPORT_ORDER_CONTRACT = (
    "The import-order contract: tests/conftest.py must call"
    " tests.support.xdist.isolate_xdist_worker() before anything imports"
    " ook.config, which snapshots the environment into its module-level"
    " Configuration() singleton as it is imported. An import that gets ahead"
    " of the shim -- one moved above the call in conftest.py, or one"
    " tests.support.xdist newly reaches transitively -- leaves every worker"
    " on the same database and consumer group, where each worker's per-test"
    " truncate clobbers the tests running in the others."
)


class XdistIsolationError(RuntimeError):
    """A pytest-xdist worker is running without its own database and topics.

    Raised at collection time by `verify_worker_isolation`, which is the only
    thing standing between a broken import order and a run whose failures look
    like flaky tests.
    """


def isolate_xdist_worker() -> None:
    """Give this pytest-xdist worker its own database and Kafka namespace.

    Under pytest-xdist every worker is a separate process that would otherwise
    share the single testcontainers PostgreSQL database and Kafka topics, and
    the per-test database reset in the ``app`` and ``factory`` fixtures would
    clobber concurrently running tests. Call this at conftest import time in
    each worker process -- before ``ook.config`` is imported, so before the
    module-level ``Configuration()`` singleton reads the environment. It:

    - creates a dedicated PostgreSQL database named after the worker
      (``<base>_gw0``, ...) on the shared container, with the ``pg_trgm``
      extension installed, and points ``OOK_DATABASE_URL`` at it; and
    - namespaces the Kafka topics and consumer group per worker so each
      worker's FastStream broker publishes and consumes independently.

    In a non-xdist (serial) run ``PYTEST_XDIST_WORKER`` is unset and this is a
    no-op: the suite runs against the base database and topics exactly as it
    would without xdist.
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    if worker_id is None:
        return

    base_database = urlsplit(os.environ["OOK_DATABASE_URL"]).path.lstrip("/")
    os.environ["OOK_DATABASE_URL"] = asyncio.run(
        provision_database(f"{base_database}_{worker_id}")
    )
    for variable, template in _WORKER_KAFKA_SETTINGS.values():
        os.environ[variable] = template.format(worker_id=worker_id)


def verify_worker_isolation(
    *,
    database_url: str,
    ingest_kafka_topic: str,
    linkcheck_kafka_topic: str,
    kafka_consumer_group_id: str,
) -> None:
    """Check that the configuration picked up this worker's isolation.

    `isolate_xdist_worker` only takes effect if it runs before ``ook.config``
    is imported, and that ordering is otherwise enforced by nothing but a
    comment -- the ``noqa: E402`` in ``tests/conftest.py`` silences the one
    lint rule that would notice an import jumping the queue. Call this from
    ``tests/conftest.py`` *after* the ``ook`` imports, passing the settings
    the shim is responsible for, so a broken order fails collection instead of
    quietly collapsing all four workers onto one database.

    Outside pytest-xdist there is nothing to isolate and this is a no-op.

    Parameters
    ----------
    database_url
        ``config.database_url``, rendered as a string.
    ingest_kafka_topic
        ``config.ingest_kafka_topic``.
    linkcheck_kafka_topic
        ``config.linkcheck_kafka_topic``.
    kafka_consumer_group_id
        ``config.kafka_consumer_group_id``.

    Raises
    ------
    XdistIsolationError
        Raised if any of those settings is not the one this worker's shim
        wrote. The message names each mismatch and the import-order contract
        that was broken.
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    if worker_id is None:
        return

    values = {
        "ingest_kafka_topic": ingest_kafka_topic,
        "linkcheck_kafka_topic": linkcheck_kafka_topic,
        "kafka_consumer_group_id": kafka_consumer_group_id,
    }
    database = urlsplit(database_url).path.lstrip("/")
    problems = []
    if not database.endswith(f"_{worker_id}"):
        problems.append(
            f"database is {database!r}, expected a name ending in"
            f" '_{worker_id}'"
        )
    for attribute, (_, template) in _WORKER_KAFKA_SETTINGS.items():
        expected = template.format(worker_id=worker_id)
        if values[attribute] != expected:
            problems.append(
                f"{attribute} is {values[attribute]!r}, expected {expected!r}"
            )
    if problems:
        raise XdistIsolationError(
            f"pytest-xdist worker {worker_id} is not isolated:"
            f" {'; '.join(problems)}. {_IMPORT_ORDER_CONTRACT}"
        )
