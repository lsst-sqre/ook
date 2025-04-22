import base64
import os
import subprocess
from pathlib import Path

import nox
from testcontainers.kafka import KafkaContainer
from testcontainers.postgres import PostgresContainer

# Default sessions
nox.options.sessions = ["lint", "typing", "test", "docs"]

# Other nox defaults
nox.options.default_venv_backend = "uv"
nox.options.reuse_existing_virtualenvs = True


@nox.session(name="venv-init", venv_backend=None, python=False)
def init_dev(session: nox.Session) -> None:
    """Set up a development venv."""
    # Create a venv in the current directory, replacing any existing one
    session.run("uv", "venv", external=True)
    _install_dev(session, bin_prefix=".venv/bin/")

    print(
        "\nTo activate this virtual env, run:\n\n\tsource .venv/bin/activate\n"
    )


@nox.session(name="init", venv_backend=None, python=False)
def init(session: nox.Session) -> None:
    """Set up the development environment in the current virtual env."""
    _install_dev(session, bin_prefix=".venv/bin/")


@nox.session
def lint(session: nox.Session) -> None:
    """Run pre-commit hooks."""
    session.run_install(
        "uv",
        "sync",
        "--only-group=lint",
        "--frozen",
        "--no-install-project",
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )
    session.run("pre-commit", "run", "--all-files", *session.posargs)


@nox.session
def typing(session: nox.Session) -> None:
    """Run mypy."""
    session.run_install(
        "uv",
        "sync",
        "--group=typing",
        "--no-default-groups",
        "--frozen",
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )
    session.run("mypy", "noxfile.py", "src", "tests")


@nox.session
def test(session: nox.Session) -> None:
    """Run pytest."""
    session.run_install(
        "uv",
        "sync",
        "--frozen",
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )

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
            session.run(
                "pytest",
                "--cov=ook",
                "--cov-branch",
                *session.posargs,
                env=env_vars,
            )


@nox.session(name="dump-db-schema")
def dump_db_schema(session: nox.Session) -> None:
    """Initialize then dump the database schema."""
    session.run_install(
        "uv",
        "sync",
        "--frozen",
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )

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


@nox.session(name="alembic")
def alembic(session: nox.Session) -> None:
    """Run alembic commands."""
    sql_dump_path = Path(__file__).parent.joinpath("alembic/schema_dump.sql")

    if not sql_dump_path.exists():
        session.error(
            "Database schema dump not found at alembic/schema_dump.sql. Run "
            "nox -s dump-db-schema with the earlier version of the "
            "application first."
        )

    session.run_install(
        "uv",
        "sync",
        "--frozen",
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )

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
            postgres.with_volume_mapping(
                sql_dump_path,
                "/docker-entrypoint-initdb.d/schema.sql",
            )
            session.run("alembic", *session.posargs, env=env_vars)


@nox.session(name="run")
def run(session: nox.Session) -> None:
    """Run the application in development mode."""
    session.run_install(
        "uv",
        "sync",
        "--no-default-groups",
        "--frozen",
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )

    with KafkaContainer().with_kraft() as kafka:
        with PostgresContainer("postgres:16") as postgres:
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
            session.run(
                "uvicorn",
                "ook.main:app",
                "--reload",
                env=env_vars,
            )


@nox.session
def docs(session: nox.Session) -> None:
    """Build the docs."""
    session.run_install(
        "uv",
        "sync",
        "--group=docs",
        "--no-default-groups",
        "--frozen",
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )
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


@nox.session(name="docs-linkcheck")
def docs_linkcheck(session: nox.Session) -> None:
    """Linkcheck the docs."""
    session.run_install(
        "uv",
        "sync",
        "--group=docs",
        "--no-default-groups",
        "--frozen",
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )
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


@nox.session(name="update-deps")
def update_deps(session: nox.Session) -> None:
    """Update pinned server dependencies and pre-commit hooks."""
    session.run("uv", "lock", "--active", "--upgrade", external=True)
    session.run(
        "uv",
        "run",
        "--only-group=lint",
        "pre-commit",
        "autoupdate",
        external=True,
    )

    print("\nTo refresh the development venv, run:\n\n\tnox -s init\n")


def _install_dev(session: nox.Session, bin_prefix: str = "") -> None:
    """Install the application and all development dependencies into the
    user's virtual environment.

    This is for setting up a development environment. Testing sessions should
    use the `uv sync` command to install dependencies into the nox virtual
    environment.
    """
    session.run("uv", "sync", "--active", "--all-groups", external=True)
    session.run("uv", "run", "pre-commit", "install", external=True)


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


def _make_env_vars(
    overrides: dict[str, str] | None = None, *, use_local_secrets: bool = False
) -> dict[str, str]:
    """Create a environment variable dictionary for test sessions that enables
    the app to start up.
    """
    env_vars = {
        "SAFIR_PROFILE": "development",
        "SAFIR_LOG_LEVEL": "DEBUG",
        "SAFIR_ENVIRONMENT_URL": "http://example.com/",
        "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
        "KAFKA_SECURITY_PROTOCOL": "PLAINTEXT",
        "OOK_ENABLE_CONSUMER": "false",
        "ALGOLIA_APP_ID": "test",
        "ALGOLIA_API_KEY": "test",
        "OOK_GITHUB_APP_ID": "1234",
        "OOK_GITHUB_APP_PRIVATE_KEY": TEST_GITHUB_APP_PRIVATE_KEY,
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
