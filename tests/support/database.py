"""Database reset helpers for tests.

Creating the full schema with ``initialize_database(..., reset=True)`` per
test (drop_all + create_all) is expensive. Instead, the schema is created
(and Alembic-stamped) once per pytest process and each test starts by
truncating every table, which preserves the empty-database isolation
guarantee at a fraction of the cost. ``TRUNCATE ... RESTART IDENTITY``
also resets sequences so tests that assert specific autoincrement IDs
behave identically to a freshly created schema.

The ``alembic_version`` table is deliberately not truncated: the
application lifespan checks ``is_database_current`` at startup, so the
stamp applied at schema-creation time must survive between tests.

``TRUNCATE`` takes an ``ACCESS EXCLUSIVE`` lock on every table, so it waits
behind *any* other session that holds a conflicting lock. The application
is shared across the whole test session, so a Kafka handler triggered by an
earlier test can still be inside a transaction when the next test resets the
database. With PostgreSQL's default (infinite) ``lock_timeout`` that wait is
unbounded: one GitHub Actions run of this suite went silent for 22 minutes
until the job timed out. Every truncate therefore runs under a short
``lock_timeout`` and is retried a bounded number of times -- background
handler transactions commit in milliseconds, so a retry practically always
wins -- and a truncate that never gets its lock raises `TruncateLockError`
naming the PIDs, states, and queries of the sessions that were in the way.
Worst case the whole thing gives up in a handful of seconds instead of
hanging the job.

Tests that run DDL (``tests/dbschema_test.py`` and ``tests/migrations``) must
not do so against the database the session-scoped application's connection
pool is attached to, because their ``DROP``/``CREATE`` statements contend for
the same table locks. They use a dedicated database instead; see
`ddl_database_url`. Those tests also leave that database's schema dropped or
structurally different from the one ``create_all`` builds, so they request the
``_rebuild_ddl_schema_after_test`` fixture, which calls `invalidate_schema` on
teardown; `reset_database_for_test` then rebuilds instead of truncating
whatever they left behind.
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit, urlunsplit

import structlog
from safir.database import initialize_database, stamp_database_async
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import TextClause

from ook.dbschema import Base

__all__ = [
    "TruncateLockError",
    "ddl_database_url",
    "invalidate_schema",
    "reset_database_for_test",
    "truncate_all_tables",
]

_LOCK_NOT_AVAILABLE = "55P03"
"""PostgreSQL SQLSTATE for ``canceling statement due to lock timeout``."""

_UNDEFINED_TABLE = "42P01"
"""PostgreSQL SQLSTATE for ``relation ... does not exist``."""

TRUNCATE_LOCK_TIMEOUT = "3s"
"""How long one truncate attempt waits for its ``ACCESS EXCLUSIVE`` locks."""

TRUNCATE_ATTEMPTS = 4
"""How many times a lock-blocked truncate is retried before giving up."""

TRUNCATE_RETRY_DELAY = 0.25
"""Seconds to wait between lock-blocked truncate attempts."""

_schema_ready: set[str] = set()
"""Names of the databases whose schema this pytest process has created."""

_ddl_database_url: str | None = None
"""URL of this pytest process's DDL database, once it has been created."""


class TruncateLockError(RuntimeError):
    """A per-test database truncate could not acquire its table locks.

    The message names the other sessions on the database so a CI failure is
    diagnosable without reproducing the race.
    """


async def ddl_database_url() -> str:
    """Return the URL of this pytest process's dedicated DDL database.

    Tests that issue DDL -- dropping the schema, running ``alembic upgrade``,
    or replaying a data migration that rebuilds foreign keys -- must not do it
    on the database the session-scoped application is connected to: their
    locks collide with the application's pooled connections and with the
    per-test truncate. This database is created on first use (with ``pg_trgm``
    installed, matching the databases the noxfile and the xdist worker shim
    provision) and is named after the current database, so under pytest-xdist
    each worker gets its own.

    Returns
    -------
    str
        Database URL, including the password from the environment, suitable
        both for `safir.database.create_database_engine` and for handing to a
        subprocess as ``OOK_DATABASE_URL``.
    """
    global _ddl_database_url
    if _ddl_database_url is not None:
        return _ddl_database_url

    import asyncpg  # noqa: PLC0415

    url = urlsplit(os.environ["OOK_DATABASE_URL"])
    base_database = url.path.lstrip("/")
    ddl_database = f"{base_database}_ddl"
    password = os.environ["OOK_DATABASE_PASSWORD"]

    connect_args = {
        "host": url.hostname,
        "port": url.port,
        "user": url.username,
        "password": password,
    }
    conn = await asyncpg.connect(database=base_database, **connect_args)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{ddl_database}"')
        await conn.execute(f'CREATE DATABASE "{ddl_database}"')
    finally:
        await conn.close()
    conn = await asyncpg.connect(database=ddl_database, **connect_args)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    finally:
        await conn.close()

    _ddl_database_url = urlunsplit(url._replace(path=f"/{ddl_database}"))
    return _ddl_database_url


def invalidate_schema(url: str) -> None:
    """Forget that this process built the schema in a database.

    Call this from any test that drops the database or structurally mutates
    its schema -- an Alembic rebuild, a data migration that recreates foreign
    keys -- so the next `reset_database_for_test` rebuilds the canonical
    ``create_all`` schema instead of truncating whatever the test left behind.
    Without it a failed rebuild leaves the database empty but still marked
    ready, and every later test on it dies in `truncate_all_tables` with
    ``UndefinedTable``, burying the real failure.

    Invalidating a database this process never built is a no-op, so a fixture
    can invalidate on teardown without caring whether the test it wraps got
    far enough to create anything.

    Parameters
    ----------
    url
        Database URL, as returned by `ddl_database_url`.
    """
    _schema_ready.discard(urlsplit(url).path.lstrip("/"))


async def reset_database_for_test(engine: AsyncEngine) -> None:
    """Give the current test an empty, Alembic-stamped database.

    The first call for a given database in a pytest process performs the full
    ``initialize_database`` (drop_all + create_all) and stamps the Alembic
    version. Subsequent calls truncate all schema tables (restarting identity
    sequences) instead, which is much faster and equivalent for test
    isolation.

    A test that drops or rebuilds the database is expected to call
    `invalidate_schema` so the next call here rebuilds the canonical schema.
    If one does not -- or fails partway through its own rebuild -- the
    truncate finds no tables; rather than propagate that ``UndefinedTable``
    (and the cascade of misleading failures it causes in every later test on
    this database), the schema is rebuilt and a warning is logged.

    Parameters
    ----------
    engine
        Database engine to use.

    Raises
    ------
    TruncateLockError
        Raised if another session held conflicting table locks for the whole
        bounded retry window.
    """
    database = engine.url.database
    assert database is not None
    logger = structlog.get_logger("ook")
    if database in _schema_ready:
        if await _truncate_unless_schema_missing(engine):
            return
        logger.warning(
            "Test database schema is gone despite being marked ready;"
            " rebuilding it. A test that drops or rebuilds this database"
            " should call tests.support.database.invalidate_schema.",
            database=database,
        )
        _schema_ready.discard(database)

    await initialize_database(engine, logger, schema=Base.metadata, reset=True)
    await stamp_database_async(engine)
    _schema_ready.add(database)


async def truncate_all_tables(
    engine: AsyncEngine,
    *,
    lock_timeout: str = TRUNCATE_LOCK_TIMEOUT,
    attempts: int = TRUNCATE_ATTEMPTS,
    retry_delay: float = TRUNCATE_RETRY_DELAY,
) -> None:
    """Truncate every schema table, bounded by a lock timeout.

    Parameters
    ----------
    engine
        Database engine to use.
    lock_timeout
        PostgreSQL ``lock_timeout`` value applied to each attempt.
    attempts
        Number of attempts before giving up.
    retry_delay
        Seconds to sleep between attempts.

    Raises
    ------
    TruncateLockError
        Raised if every attempt was cancelled by the lock timeout. The message
        names the other sessions on the database.
    """
    tables = ", ".join(
        f'"{table.name}"' for table in Base.metadata.sorted_tables
    )
    statement = text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    for attempt in range(1, attempts + 1):
        error = await _attempt_truncate(engine, statement, lock_timeout)
        if error is None:
            return
        if attempt == attempts:
            blockers = await _describe_other_sessions(engine)
            raise TruncateLockError(
                f"Could not truncate the test database after {attempts}"
                f" attempts with lock_timeout={lock_timeout}: another session"
                " is holding conflicting table locks. Sessions on"
                f" {engine.url.database}:\n{blockers}"
            ) from error
        await asyncio.sleep(retry_delay)


async def _truncate_unless_schema_missing(engine: AsyncEngine) -> bool:
    """Truncate every table, reporting whether the schema was still there.

    Returns
    -------
    bool
        `True` if the truncate succeeded, `False` if the schema's tables no
        longer exist. Every other database error, including a lock timeout
        that exhausted its retries, propagates.
    """
    try:
        await truncate_all_tables(engine)
    except DBAPIError as exc:
        if not _is_undefined_table(exc):
            raise
        return False
    return True


async def _attempt_truncate(
    engine: AsyncEngine, statement: TextClause, lock_timeout: str
) -> DBAPIError | None:
    """Run one truncate attempt, returning its lock-timeout error, if any.

    Any other database error propagates: only a lock timeout is worth
    retrying.
    """
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(f"SET LOCAL lock_timeout = '{lock_timeout}'")
            )
            await conn.execute(statement)
    except DBAPIError as exc:
        if not _is_lock_timeout(exc):
            raise
        return exc
    return None


def _has_sqlstate(exc: DBAPIError, sqlstate: str) -> bool:
    """Return whether a database error carries a given PostgreSQL SQLSTATE.

    Depending on how deeply SQLAlchemy wrapped the asyncpg exception, the code
    may live on ``orig`` or on the exception that caused it, and may be spelled
    either ``sqlstate`` (asyncpg) or ``pgcode`` (SQLAlchemy's translation).
    """
    candidates = [exc.orig, getattr(exc.orig, "__cause__", None)]
    for candidate in candidates:
        if candidate is None:
            continue
        code = getattr(candidate, "sqlstate", None) or getattr(
            candidate, "pgcode", None
        )
        if code == sqlstate:
            return True
    return False


def _is_lock_timeout(exc: DBAPIError) -> bool:
    """Return whether a database error is a ``lock_timeout`` cancellation."""
    if _has_sqlstate(exc, _LOCK_NOT_AVAILABLE):
        return True
    return "due to lock timeout" in str(exc)


def _is_undefined_table(exc: DBAPIError) -> bool:
    """Return whether a database error is a missing-relation error.

    Only the SQLSTATE is consulted: PostgreSQL says "does not exist" about
    many other objects too, so matching on the message would misclassify them.
    """
    return _has_sqlstate(exc, _UNDEFINED_TABLE)


async def _describe_other_sessions(engine: AsyncEngine) -> str:
    """Return a report of the other sessions connected to this database.

    Any of them holding an open transaction is a candidate blocker of the
    truncate that just timed out; ``pg_blocking_pids`` additionally shows
    lock chains among them.
    """
    query = text(
        "SELECT pid, state, wait_event_type, wait_event,"
        " pg_blocking_pids(pid) AS blocked_by,"
        " extract(epoch from (now() - xact_start)) AS xact_age,"
        " left(query, 300) AS query"
        " FROM pg_stat_activity"
        " WHERE datname = current_database() AND pid <> pg_backend_pid()"
        " ORDER BY xact_start NULLS LAST"
    )
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
    except DBAPIError as exc:  # pragma: no cover - diagnostics must not mask
        return f"(could not query pg_stat_activity: {exc})"
    if not rows:
        return "(no other sessions; the blocker disconnected)"
    lines = []
    for row in rows:
        age = row["xact_age"]
        age_text = "no transaction" if age is None else f"xact {age:.1f}s old"
        lines.append(
            f"  pid={row['pid']} state={row['state']} {age_text}"
            f" wait={row['wait_event_type']}/{row['wait_event']}"
            f" blocked_by={list(row['blocked_by'])}\n"
            f"    query: {row['query']!r}"
        )
    return "\n".join(lines)
