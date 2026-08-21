from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from time import sleep
from typing import TYPE_CHECKING

import nox
import sqlalchemy
from nox_uv import session

if TYPE_CHECKING:
    from testcontainers.postgres import PostgresContainer

# Default sessions
nox.options.sessions = ["lint", "typing", "test", "docs"]

# Other nox defaults
nox.options.default_venv_backend = "uv"
nox.options.reuse_existing_virtualenvs = True

# Test paths that require the Postgres and Kafka testcontainers. Every other
# path under tests/ is treated as a pure unit test and runs in the
# "test-unit" session without starting any containers. A new test directory
# is picked up by test-unit automatically; if its tests actually need the
# database or Kafka they fail immediately there (connection refused against
# the unreachable placeholder servers below) until the path is added here.
INFRA_TEST_PATHS = (
    "tests/cli",
    "tests/dbschema_test.py",
    "tests/factory_test.py",
    "tests/handlers",
    "tests/kafka_drain_barrier_test.py",
    "tests/migrations",
    "tests/provision_database_test.py",
    "tests/schema_reset_test.py",
    "tests/services",
    "tests/shared_engine_test.py",
    "tests/storage",
    "tests/truncate_lock_test.py",
)

# Placeholder connection targets for the test-unit session. Nothing listens
# on port 1, so any test that reaches for the database or Kafka fails
# immediately with a connection error instead of hanging or silently using
# real infrastructure.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://ook@127.0.0.1:1/ook"
UNREACHABLE_KAFKA_BOOTSTRAP = "127.0.0.1:1"


@session(uv_only_groups=["lint"], uv_no_install_project=True)
def lint(session: nox.Session) -> None:
    """Run pre-commit hooks with prek."""
    session.run("prek", "run", "--all-files", *session.posargs)


@session(uv_groups=["typing", "dev"])
def typing(session: nox.Session) -> None:
    """Run mypy."""
    session.run("mypy", "noxfile.py", "src", "tests")


def _xdist_args(posargs: list[str]) -> list[str]:
    """Return default pytest-xdist arguments for a pytest invocation.

    The suite runs under pytest-xdist with 4 workers by default (matching
    the 4 vCPUs of GitHub Actions ubuntu-latest runners); each worker gets
    its own PostgreSQL database and Kafka topic namespace via the worker
    isolation shim in tests/conftest.py. Pass your own ``-n`` in posargs
    (e.g. ``-n 0`` for a serial run) to override.
    """
    if any(
        arg == "-n" or arg.startswith(("-n", "--numprocesses"))
        for arg in posargs
    ):
        return []
    return ["-n", "4"]


@session(uv_groups=["dev"])
def test(session: nox.Session) -> None:
    """Run pytest without coverage reporting."""
    _setup_testcontainers_logging()
    _setup_testcontainers_env()

    # Import after setting environment variables so config is read correctly
    from testcontainers.kafka import KafkaContainer  # noqa: PLC0415
    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415

    with KafkaContainer().with_kraft() as kafka:
        # Autovacuum is disabled because its ANALYZE can race a test's
        # uncommitted bulk seed ingest: it records the table as N pages
        # holding zero live tuples, and after the ingest commits the
        # planner still estimates ~0 rows, picking nested-loop plans
        # that turn millisecond link queries into multi-minute ones
        # (per-test TRUNCATE keeps tables alive all session, so stats
        # are never corrected the way per-test DROP/CREATE hid this).
        # Tests never run long enough to need vacuuming.
        with PostgresContainer("postgres:16").with_command(
            "postgres -c autovacuum=off"
        ) as postgres:
            _install_postgres_extensions(postgres)

            env_vars = _make_env_vars(
                {
                    "KAFKA_BOOTSTRAP_SERVERS": kafka.get_bootstrap_server(),
                    "OOK_DATABASE_URL": postgres.get_connection_url(
                        driver="asyncpg"
                    ),
                    "OOK_DATABASE_PASSWORD": postgres.password,
                }
            )
            session.run(
                "pytest",
                *_xdist_args(session.posargs),
                *session.posargs,
                env=env_vars,
            )


@session(name="test-coverage", uv_groups=["dev"])
def test_coverage(session: nox.Session) -> None:
    """Run pytest with coverage reporting."""
    _setup_testcontainers_logging()
    _setup_testcontainers_env()

    # Import after setting environment variables so config is read correctly
    from testcontainers.kafka import KafkaContainer  # noqa: PLC0415
    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415

    with KafkaContainer().with_kraft() as kafka:
        # Autovacuum is disabled because its ANALYZE can race a test's
        # uncommitted bulk seed ingest: it records the table as N pages
        # holding zero live tuples, and after the ingest commits the
        # planner still estimates ~0 rows, picking nested-loop plans
        # that turn millisecond link queries into multi-minute ones
        # (per-test TRUNCATE keeps tables alive all session, so stats
        # are never corrected the way per-test DROP/CREATE hid this).
        # Tests never run long enough to need vacuuming.
        with PostgresContainer("postgres:16").with_command(
            "postgres -c autovacuum=off"
        ) as postgres:
            _install_postgres_extensions(postgres)

            env_vars = _make_env_vars(
                {
                    "KAFKA_BOOTSTRAP_SERVERS": kafka.get_bootstrap_server(),
                    "OOK_DATABASE_URL": postgres.get_connection_url(
                        driver="asyncpg"
                    ),
                    "OOK_DATABASE_PASSWORD": postgres.password,
                }
            )
            session.run(
                "pytest",
                "--cov=ook",
                "--cov-branch",
                *_xdist_args(session.posargs),
                *session.posargs,
                env=env_vars,
            )


# Pytest options that take their value as a separate argument. The argument
# after one of these is that option's value, never a test path: ``-k tests``
# is a keyword expression, not a request to collect the tests directory.
PYTEST_VALUE_OPTIONS = frozenset(
    {
        "-c",
        "--config-file",
        "--deselect",
        "--dist",
        "-k",
        "-m",
        "--ignore",
        "--ignore-glob",
        "--maxfail",
        "-n",
        "--numprocesses",
        "-o",
        "--override-ini",
        "-p",
        "--rootdir",
    }
)


def _path_selections(posargs: Sequence[str]) -> list[str]:
    """Return the posargs that select tests by path.

    A posarg selects tests when it is neither an option nor an option's
    value and it names a file or directory that exists, once any ``::``
    node identifier is stripped. Everything else -- ``-x``, ``-k expr``,
    ``-n 2`` -- is a way of running the selection, not a selection.
    """
    selections: list[str] = []
    skip_value = False
    for arg in posargs:
        if skip_value:
            skip_value = False
        elif arg.startswith("-"):
            skip_value = arg in PYTEST_VALUE_OPTIONS
        elif Path(arg.split("::", 1)[0]).exists():
            selections.append(arg)
    return selections


def _infra_path_for(selection: str) -> str | None:
    """Return the INFRA_TEST_PATHS entry covering ``selection``, if any."""
    path = Path(selection.split("::", 1)[0])
    if path.is_absolute():
        try:
            path = path.relative_to(Path.cwd())
        except ValueError:
            return None
    relative = PurePosixPath(path.as_posix())
    for infra_path in INFRA_TEST_PATHS:
        infra = PurePosixPath(infra_path)
        if relative == infra or infra in relative.parents:
            return infra_path
    return None


def _requested_xdist_workers(posargs: Sequence[str]) -> str | None:
    """Return the worker count ``posargs`` asks pytest-xdist for, if any."""
    for index, arg in enumerate(posargs):
        if arg in {"-n", "--numprocesses"}:
            return posargs[index + 1] if index + 1 < len(posargs) else ""
        if arg.startswith("--numprocesses="):
            return arg.removeprefix("--numprocesses=")
        if arg.startswith("-n") and arg != "-n":
            return arg.removeprefix("-n")
    return None


def _unit_test_args(posargs: Sequence[str]) -> list[str]:
    """Compose the pytest arguments for the test-unit session.

    The INFRA_TEST_PATHS ignore flags always apply, so an invocation that
    only passes options (``nox -s test-unit -- -x``) still runs the whole
    infrastructure-free selection rather than the entire suite against the
    unreachable placeholder servers. A posarg that names a path replaces
    the default ``tests`` selection instead of adding to it.

    Raises
    ------
    ValueError
        Raised if a posarg names a path this session ignores, which would
        otherwise collect nothing at all, or asks for pytest-xdist workers,
        which this session has no database to give them.
    """
    workers = _requested_xdist_workers(posargs)
    if workers is not None and workers != "0":
        raise ValueError(
            "this session cannot run under pytest-xdist, because the"
            " worker isolation shim gives each worker its own database"
            " and this session starts no database at all. It runs the"
            " whole unit selection serially in about a second"
        )
    selections = _path_selections(posargs)
    for selection in selections:
        infra_path = _infra_path_for(selection)
        if infra_path is not None:
            raise ValueError(
                f"{selection} is under {infra_path}, which needs the Kafka"
                " and PostgreSQL containers that this session does not"
                f" start. Run 'nox -s test -- {selection}' instead"
            )
    ignore_flags = [f"--ignore={path}" for path in INFRA_TEST_PATHS]
    if selections:
        return [*posargs, *ignore_flags]
    return ["tests", *posargs, *ignore_flags]


@session(name="test-unit", uv_groups=["dev"])
def test_unit(session: nox.Session) -> None:
    """Run the unit tests that need no testcontainers.

    Runs everything under tests/ except INFRA_TEST_PATHS, without starting
    the Kafka or Postgres containers. The database and Kafka environment
    variables point at unreachable placeholder servers, so a test that is
    misclassified as a unit test (that is, one that actually touches the
    database or Kafka) fails immediately with a connection error.

    The session runs serially: it takes about a second, so the xdist
    worker startup that the container-backed sessions need would be pure
    overhead, and each worker would want a database of its own that this
    session never starts. Asking for workers is therefore an error.

    Positional arguments go to pytest with the ignore list still applied:
    options alone (``nox -s test-unit -- -x``) narrow how the unit
    selection runs, while a path (``nox -s test-unit --
    tests/domain/base32id_test.py``) narrows what it selects. Naming a
    path that needs the containers is an error pointing at ``nox -s
    test``.
    """
    env_vars = _make_env_vars(
        {
            "KAFKA_BOOTSTRAP_SERVERS": UNREACHABLE_KAFKA_BOOTSTRAP,
            "OOK_DATABASE_URL": UNREACHABLE_DATABASE_URL,
            "OOK_DATABASE_PASSWORD": "unreachable",
        }
    )
    try:
        pytest_args = _unit_test_args(session.posargs)
    except ValueError as e:
        session.error(str(e))
    session.run(
        "pytest",
        *pytest_args,
        env=env_vars,
    )


@session(name="dump-db-schema")
def dump_db_schema(session: nox.Session) -> None:
    """Initialize then dump the database schema."""
    _setup_testcontainers_logging()
    _setup_testcontainers_env()

    # Import after setting environment variables so config is read correctly
    from testcontainers.kafka import KafkaContainer  # noqa: PLC0415
    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415

    with KafkaContainer().with_kraft() as kafka:
        with PostgresContainer("postgres:16") as postgres:
            env_vars = _make_env_vars(
                {
                    "KAFKA_BOOTSTRAP_SERVERS": kafka.get_bootstrap_server(),
                    "OOK_DATABASE_URL": postgres.get_connection_url(
                        driver="asyncpg"
                    ),
                    "OOK_DATABASE_PASSWORD": postgres.password,
                }
            )
            _install_postgres_extensions(postgres)

            session.run(
                "ook",
                "init",
                "--alembic-config-path",
                "alembic.ini",
                env=env_vars,
            )

            # Use docker exec to run pg_dump inside the container
            # We're not using the -s flag to dump schema only because we want
            # to include the data for the alembic version table.
            dump_command = [
                "docker",
                "exec",
                postgres._container.id,  # noqa: SLF001
                "pg_dump",
                "-U",
                postgres.username,
                postgres.dbname,
            ]

            with (
                Path(__file__)
                .parent.joinpath("alembic/schema_dump.sql")
                .open("wb") as f
            ):
                subprocess.run(dump_command, stdout=f, check=True)

            session.log("Database schema dumped to alembic/schema_dump.sql")


@session(name="alembic", uv_groups=["dev"])
def alembic(session: nox.Session) -> None:
    """Run alembic commands."""
    _setup_testcontainers_logging()
    _setup_testcontainers_env()

    sql_dump_path = Path(__file__).parent.joinpath("alembic/schema_dump.sql")

    if not sql_dump_path.exists():
        session.error(
            "Database schema dump not found at alembic/schema_dump.sql. Run "
            "nox -s dump-db-schema with the earlier version of the "
            "application first."
        )

    # Import after setting environment variables so config is read correctly
    from testcontainers.kafka import KafkaContainer  # noqa: PLC0415
    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415

    with KafkaContainer().with_kraft() as kafka:
        with PostgresContainer("postgres:16").with_volume_mapping(
            str(sql_dump_path), "/docker-entrypoint-initdb.d/schema.sql"
        ) as postgres:
            session.log(
                "Postgres connection URL: "
                f"{postgres.get_connection_url(driver=None)}"
            )
            env_vars = _make_env_vars(
                {
                    "KAFKA_BOOTSTRAP_SERVERS": kafka.get_bootstrap_server(),
                    "OOK_DATABASE_URL": postgres.get_connection_url(
                        driver="asyncpg"
                    ),
                    "OOK_DATABASE_PASSWORD": postgres.password,
                }
            )
            _install_postgres_extensions(postgres)

            sleep(1)
            engine = sqlalchemy.create_engine(postgres.get_connection_url())
            with engine.begin() as connection:
                result = connection.execute(
                    sqlalchemy.text(
                        "SELECT version_num FROM "
                        "public.alembic_version LIMIT 1;"
                    )
                )
                row = result.fetchone()
                if row:
                    session.log(
                        f"Current alembic version: {row[0]}",
                    )
                else:
                    session.error("No alembic version found.")

                # Print the current database schema
                session.log("Current database schema:")
                schema_query = sqlalchemy.text(
                    """
                    SELECT
                        table_schema,
                        table_name,
                        column_name,
                        data_type,
                        is_nullable
                    FROM
                        information_schema.columns
                    WHERE
                        table_schema = 'public'
                    ORDER BY
                        table_name,
                        ordinal_position;
                    """
                )
                schema_result = connection.execute(schema_query)
                for row in schema_result:
                    session.log(
                        f"  {row.table_name}.{row.column_name}: "
                        f"{row.data_type} (nullable: {row.is_nullable})"
                    )

            session.run("alembic", *session.posargs, env=env_vars)


@session(name="run")
def run(session: nox.Session) -> None:
    """Run the application in development mode.

    Pass a port number as an argument to customize the port (default: 3001)::

        nox -s run -- 8080
    """
    _setup_testcontainers_logging()
    _setup_testcontainers_env()

    # Import after setting environment variables so config is read correctly
    from testcontainers.kafka import KafkaContainer  # noqa: PLC0415
    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415

    with KafkaContainer().with_kraft() as kafka:
        with PostgresContainer(
            "postgres:16", username="user", password="pass"
        ) as postgres:
            _install_postgres_extensions(postgres)
            session.log(
                "Postgres connection URL: "
                f"{postgres.get_connection_url(driver='asyncpg')}"
            )

            env_vars = _make_env_vars(
                {
                    "KAFKA_BOOTSTRAP_SERVERS": kafka.get_bootstrap_server(),
                    "OOK_DATABASE_URL": postgres.get_connection_url(
                        driver="asyncpg"
                    ),
                    "OOK_DATABASE_PASSWORD": postgres.password,
                },
                use_local_secrets=True,
            )
            session.run(
                "ook",
                "init",
                "--alembic-config-path",
                "alembic.ini",
                env=env_vars,
            )

            # Use port from posargs or default to 3001
            port = "3001"
            if session.posargs:
                # Check if any posarg looks like a port number
                for arg in session.posargs:
                    if arg.isdigit() and 1024 <= int(arg) <= 65535:
                        port = arg
                        break

            session.run(
                "uvicorn",
                "ook.main:app",
                "--reload",
                "--port",
                port,
                env=env_vars,
            )


@session(name="cli")
def cli(session: nox.Session) -> None:
    """Run an ook CLI command.

    Pass argument to the Ook CLI as positional arguments to this session::

      op run --env-file="square.env" -- nox -s cli -- --help
    """
    _setup_testcontainers_logging()
    _setup_testcontainers_env()

    # Import after setting environment variables so config is read correctly
    from testcontainers.kafka import KafkaContainer  # noqa: PLC0415
    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415

    with KafkaContainer().with_kraft() as kafka:
        with PostgresContainer(
            "postgres:16", username="user", password="pass"
        ) as postgres:
            _install_postgres_extensions(postgres)
            session.log(
                "Postgres connection URL: "
                f"{postgres.get_connection_url(driver='asyncpg')}"
            )

            env_vars = _make_env_vars(
                {
                    "KAFKA_BOOTSTRAP_SERVERS": kafka.get_bootstrap_server(),
                    "OOK_DATABASE_URL": postgres.get_connection_url(
                        driver="asyncpg"
                    ),
                    "OOK_DATABASE_PASSWORD": postgres.password,
                },
                use_local_secrets=True,
            )
            session.run(
                "ook",
                *session.posargs,
                env=env_vars,
            )


@session(uv_groups=["docs"])
def docs(session: nox.Session) -> None:
    """Build the docs."""
    _setup_testcontainers_logging()
    _setup_testcontainers_env()

    # Import after setting environment variables so config is read correctly
    from testcontainers.kafka import KafkaContainer  # noqa: PLC0415
    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415

    doctree_dir = (session.cache_dir / "doctrees").absolute()

    with KafkaContainer().with_kraft() as kafka:
        with PostgresContainer("postgres:16") as postgres:
            env_vars = _make_env_vars(
                {
                    "KAFKA_BOOTSTRAP_SERVERS": kafka.get_bootstrap_server(),
                    "OOK_DATABASE_URL": postgres.get_connection_url(
                        driver="asyncpg"
                    ),
                    "OOK_DATABASE_PASSWORD": postgres.password,
                }
            )
            with session.chdir("docs"):
                session.run(
                    "sphinx-build",
                    # "-W", # Disable warnings-as-errors for now
                    "--keep-going",
                    "-n",
                    "-T",
                    "-b",
                    "html",
                    "-d",
                    str(doctree_dir),
                    ".",
                    "./_build/html",
                    env=env_vars,
                )


@session(name="docs-linkcheck", uv_groups=["docs"])
def docs_linkcheck(session: nox.Session) -> None:
    """Linkcheck the docs."""
    _setup_testcontainers_logging()
    _setup_testcontainers_env()

    # Import after setting environment variables so config is read correctly
    from testcontainers.kafka import KafkaContainer  # noqa: PLC0415
    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415

    doctree_dir = (session.cache_dir / "doctrees").absolute()
    with KafkaContainer().with_kraft() as kafka:
        with PostgresContainer("postgres:16") as postgres:
            env_vars = _make_env_vars(
                {
                    "KAFKA_BOOTSTRAP_SERVERS": kafka.get_bootstrap_server(),
                    "OOK_DATABASE_URL": postgres.get_connection_url(
                        driver="asyncpg"
                    ),
                    "OOK_DATABASE_PASSWORD": postgres.password,
                }
            )
            with session.chdir("docs"):
                session.run(
                    "sphinx-build",
                    # "-W",  # Disable warnings-as-errors for now
                    "--keep-going",
                    "-n",
                    "-T",
                    "-b",
                    "linkcheck",
                    "-d",
                    str(doctree_dir),
                    ".",
                    "./_build/html",
                    env=env_vars,
                )


@nox.session(name="scriv-create")
def scriv_create(session: nox.Session) -> None:
    """Create a scriv entry."""
    session.install("scriv")
    session.run("scriv", "create")


@nox.session(name="scriv-collect")
def scriv_collect(session: nox.Session) -> None:
    """Collect scriv entries."""
    session.install("scriv")
    session.run("scriv", "collect", "--add", "--version", *session.posargs)


TEST_GITHUB_APP_PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEpQIBAAKCAQEA1HgzBfJv2cOjQryCwe8NEelriOTNFWKZUivevUrRhlqcmZJd
CvuCJRr+xCN+OmO8qwgJJR98feNujxVg+J9Ls3/UOA4HcF9nYH6aqVXELAE8Hk/A
Lvxi96ms1DDuAvQGaYZ+lANxlvxeQFOZSbjkz/9mh8aLeGKwqJLp3p+OhUBQpwvA
UAPg82+OUtgTW3nSljjeFr14B8qAneGSc/wl0ni++1SRZUXFSovzcqQOkla3W27r
rLfrD6LXgj/TsDs4vD1PnIm1zcVenKT7TfYI17bsG/O/Wecwz2Nl19pL7gDosNru
F3ogJWNq1Lyn/ijPQnkPLpZHyhvuiycYcI3DiQIDAQABAoIBAQCt9uzwBZ0HVGQs
lGULnUu6SsC9iXlR9TVMTpdFrij4NODb7Tc5cs0QzJWkytrjvB4Se7XhK3KnMLyp
cvu/Fc7J3fRJIVN98t+V5pOD6rGAxlIPD4Vv8z6lQcw8wQNgb6WAaZriXh93XJNf
YBO2hSj0FU5CBZLUsxmqLQBIQ6RR/OUGAvThShouE9K4N0vKB2UPOCu5U+d5zS3W
44Q5uatxYiSHBTYIZDN4u27Nfo5WA+GTvFyeNsO6tNNWlYfRHSBtnm6SZDY/5i4J
fxP2JY0waM81KRvuHTazY571lHM/TTvFDRUX5nvHIu7GToBKahfVLf26NJuTZYXR
5c09GAXBAoGBAO7a9M/dvS6eDhyESYyCjP6w61jD7UYJ1fudaYFrDeqnaQ857Pz4
BcKx3KMmLFiDvuMgnVVj8RToBGfMV0zP7sDnuFRJnWYcOeU8e2sWGbZmWGWzv0SD
+AhppSZThU4mJ8aa/tgsepCHkJnfoX+3wN7S9NfGhM8GDGxTHJwBpxINAoGBAOO4
ZVtn9QEblmCX/Q5ejInl43Y9nRsfTy9lB9Lp1cyWCJ3eep6lzT60K3OZGVOuSgKQ
vZ/aClMCMbqsAAG4fKBjREA6p7k4/qaMApHQum8APCh9WPsKLaavxko8ZDc41kZt
hgKyUs2XOhW/BLjmzqwGryidvOfszDwhH7rNVmRtAoGBALYGdvrSaRHVsbtZtRM3
imuuOCx1Y6U0abZOx9Cw3PIukongAxLlkL5G/XX36WOrQxWkDUK930OnbXQM7ZrD
+5dW/8p8L09Zw2VHKmb5eK7gYA1hZim4yJTgrdL/Y1+jBDz+cagcfWsXZMNfAZxr
VLh628x0pVF/sof67pqVR9UhAoGBAMcQiLoQ9GJVhW1HMBYBnQVnCyJv1gjBo+0g
emhrtVQ0y6+FrtdExVjNEzboXPWD5Hq9oKY+aswJnQM8HH1kkr16SU2EeN437pQU
zKI/PtqN8AjNGp3JVgLioYp/pHOJofbLA10UGcJTMpmT9ELWsVA8P55X1a1AmYDu
y9f2bFE5AoGAdjo95mB0LVYikNPa+NgyDwLotLqrueb9IviMmn6zKHCwiOXReqXD
X9slB8RA15uv56bmN04O//NyVFcgJ2ef169GZHiRFIgIy0Pl8LYkMhCYKKhyqM7g
xN+SqGqDTKDC22j00S7jcvCaa1qadn1qbdfukZ4NXv7E2d/LO0Y2Kkc=
-----END RSA PRIVATE KEY-----

"""


def _setup_testcontainers_logging() -> None:
    """Configure logging to reduce testcontainers noise."""
    logging.getLogger("testcontainers").setLevel(logging.ERROR)
    logging.getLogger("docker").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.ERROR)


def _setup_testcontainers_env() -> None:
    """Configure testcontainers environment variables for Colima on macOS.

    This must be called before any containers are started to ensure the
    Reaper can connect properly when using Colima as the Docker runtime.
    """
    # Set testcontainers host override for Colima on macOS
    # This fixes "nodename nor servname provided, or not known" errors
    docker_host = os.getenv("DOCKER_HOST", "")
    m = re.search(r"\.colima/(?P<profile>[^/]+)/docker\.sock$", docker_host)
    if m:
        # Extract the Colima VM IP address for the active profile.
        # colima ls -j emits one JSON object per line (one per profile).
        try:
            result = subprocess.run(
                ["colima", "ls", "-j"],
                capture_output=True,
                text=True,
                check=True,
            )
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                colima_info = json.loads(line)
                if colima_info.get("name") == m["profile"] and colima_info.get(
                    "address"
                ):
                    os.environ["TESTCONTAINERS_HOST_OVERRIDE"] = colima_info[
                        "address"
                    ]
                    break
        except (
            subprocess.CalledProcessError,
            json.JSONDecodeError,
            KeyError,
        ):
            # If we can't get the Colima address, don't set override
            pass


def _make_env_vars(
    overrides: dict[str, str] | None = None, *, use_local_secrets: bool = False
) -> dict[str, str]:
    """Create a environment variable dictionary for test sessions that enables
    the app to start up.
    """
    env_vars = {
        "SAFIR_PROFILE": "development",
        "SAFIR_LOG_LEVEL": "DEBUG",
        "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
        "KAFKA_SECURITY_PROTOCOL": "PLAINTEXT",
        "OOK_ENABLE_CONSUMER": "false",
        "ALGOLIA_APP_ID": "test",
        "ALGOLIA_API_KEY": "test",
        "OOK_GITHUB_APP_ID": "1234",
        "OOK_GITHUB_APP_PRIVATE_KEY": TEST_GITHUB_APP_PRIVATE_KEY,
        # Turn off the link checker's per-host politeness delay. The whole
        # test session shares one UrlChecker, so its host schedule would
        # space every check of example.com a second apart across the entire
        # suite -- a delay the per-test Kafka drain barrier would then have
        # to wait out. Politeness itself is covered by unit tests that build
        # their own checker with an explicit interval.
        "OOK_LINKCHECK_HOST_INTERVAL": "0s",
    }
    if overrides:
        env_vars.update(overrides)

    # Load available environment variables - typically from a .env file and
    # the 1Password CLI. See `square.env` for more details.
    if use_local_secrets:
        if github_app_id := os.getenv("OOK_GITHUB_APP_ID"):
            env_vars["OOK_GITHUB_APP_ID"] = github_app_id
        if github_app_private_key := os.getenv("OOK_GITHUB_APP_PRIVATE_KEY"):
            decoded_key = base64.b64decode(github_app_private_key).decode(
                "utf-8"
            )
            env_vars["OOK_GITHUB_APP_PRIVATE_KEY"] = decoded_key
        if algolia_app_id := os.getenv("ALGOLIA_APP_ID"):
            env_vars["ALGOLIA_APP_ID"] = algolia_app_id
        if algolia_api_key := os.getenv("ALGOLIA_API_KEY"):
            env_vars["ALGOLIA_API_KEY"] = algolia_api_key
    return env_vars


def _install_postgres_extensions(postgres: PostgresContainer) -> None:
    """Install PostgreSQL extensions in the container."""
    sleep(1)
    engine = sqlalchemy.create_engine(postgres.get_connection_url())
    with engine.begin() as connection:
        connection.execute(
            sqlalchemy.text("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        )
