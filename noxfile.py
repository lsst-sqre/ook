import nox

# Default sessions
nox.options.sessions = ["lint", "typing", "test", "docs"]

# Other nox defaults
nox.options.default_venv_backend = "venv"
nox.options.reuse_existing_virtualenvs = True


# Pip installable dependencies
PIP_DEPENDENCIES = [
    ("--upgrade", "pip", "setuptools", "wheel"),
    ("-r", "requirements/main.txt"),
    ("-r", "requirements/dev.txt"),
    ("-e", "."),
]


def _install(session: nox.Session) -> None:
    """Install the application and all dependencies into the session."""
    for deps in PIP_DEPENDENCIES:
        session.install(*deps)


def _make_env_vars() -> dict[str, str]:
    """Create a environment variable dictionary for test sessions that enables
    the app to start up.
    """
    return {
        "SAFIR_PROFILE": "development",
        "SAFIR_LOG_LEVEL": "DEBUG",
        "SAFIR_ENVIRONMENT_URL": "http://example.com/",
        "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
        "OOK_REGISTRY_URL": "http://localhost:8081",
        "OOK_ENABLE_CONSUMER": "false",
    }


def _install_dev(session: nox.Session, bin_prefix: str = "") -> None:
    """Install the application and all development dependencies into the
    session.
    """
    python = f"{bin_prefix}python"
    precommit = f"{bin_prefix}pre-commit"

    # Install dev dependencies
    for deps in PIP_DEPENDENCIES:
        session.run(python, "-m", "pip", "install", *deps, external=True)
    session.run(
        python, "-m", "pip", "install", "nox", "pre-commit", external=True
    )
    # Install pre-commit hooks
    session.run(precommit, "install", external=True)


@nox.session(name="venv-init")
def init_dev(session: nox.Session) -> None:
    """Set up a development venv."""
    # Create a venv in the current directory, replacing any existing one
    session.run("python", "-m", "venv", ".venv", "--clear")
    _install_dev(session, bin_prefix=".venv/bin/")

    print(
        "\nTo activate this virtual env, run:\n\n\tsource .venv/bin/activate\n"
    )


@nox.session(name="init", venv_backend=None, python=False)
def init(session: nox.Session) -> None:
    """Set up the development environment in the current virtual env."""
    _install_dev(session, bin_prefix="")


@nox.session
def lint(session: nox.Session) -> None:
    """Run pre-commit hooks."""
    session.install("pre-commit")
    session.run("pre-commit", "run", "--all-files", *session.posargs)


@nox.session
def typing(session: nox.Session) -> None:
    """Run mypy."""
    _install(session)
    session.install("mypy")
    session.run("mypy", "noxfile.py", "src", "tests")


@nox.session
def test(session: nox.Session) -> None:
    """Run pytest."""
    _install(session)
    session.run(
        "pytest",
        "--cov=ook",
        "--cov-branch",
        *session.posargs,
        env=_make_env_vars(),
    )


@nox.session
def docs(session: nox.Session) -> None:
    """Build the docs."""
    _install(session)
    doctree_dir = (session.cache_dir / "doctrees").absolute()
    with session.chdir("docs"):
        session.run(
            "sphinx-build",
            "-W",
            "--keep-going",
            "-n",
            "-T",
            "-b",
            "html",
            "-d",
            str(doctree_dir),
            ".",
            "./_build/html",
            env=_make_env_vars(),
        )


@nox.session(name="docs-linkcheck")
def docs_linkcheck(session: nox.Session) -> None:
    """Linkcheck the docs."""
    _install(session)
    doctree_dir = (session.cache_dir / "doctrees").absolute()
    with session.chdir("docs"):
        session.run(
            "sphinx-build",
            "-W",
            "--keep-going",
            "-n",
            "-T",
            "-b",
            "linkcheck",
            "-d",
            str(doctree_dir),
            ".",
            "./_build/html",
            env=_make_env_vars(),
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
    session.install(
        "--upgrade", "pip-tools", "pip", "setuptools", "wheel", "pre-commit"
    )
    session.run("pre-commit", "autoupdate")

    # Dependencies are unpinned for compatibility with the unpinned client
    # dependency.
    session.run(
        "pip-compile",
        "--upgrade",
        "--build-isolation",
        "--allow-unsafe",
        "--resolver=backtracking",
        "--output-file",
        "requirements/main.txt",
        "requirements/main.in",
    )

    session.run(
        "pip-compile",
        "--upgrade",
        "--build-isolation",
        "--allow-unsafe",
        "--resolver=backtracking",
        "--output-file",
        "requirements/dev.txt",
        "requirements/dev.in",
    )

    print("\nTo refresh the development venv, run:\n\n\tnox -s init\n")


@nox.session(name="run")
def run(session: nox.Session) -> None:
    """Run the application in development mode."""
    # Note this doesn't work right now because Kafka is needed for the app.
    _install(session)
    session.run(
        "uvicorn",
        "ook.main:app",
        "--reload",
        env=_make_env_vars(),
    )
