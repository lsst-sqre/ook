"""Tests for the pytest arguments and environment the nox sessions compose.

The ``test`` session detects a default worker count that an invocation
can turn down, and the ``test-unit`` session runs the
infrastructure-free part of the suite against unreachable placeholder
servers -- which only holds while the ``--ignore`` flags for
`INFRA_TEST_PATHS` survive whatever posargs a developer passes. The
sessions also split their environment in two: settings a real run can live
with, and settings only a pytest run may have. These tests load
``noxfile.py`` and pin all three compositions down.

Nox itself is not installed in the test environment -- it lives in the
``nox`` dependency group, which bootstraps the sessions from outside -- so
the two modules the noxfile imports for its decorators are stubbed before
it is loaded.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
import types
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from .support.unitsession import UNIT_SESSION_ENV

_REPO_ROOT = Path(__file__).parents[1]

PytestArgs = Callable[[Sequence[str]], list[str]]
"""The signature of the noxfile's argument composers."""

SessionEnv = Callable[[], dict[str, str]]
"""The signature of the noxfile's environment composer."""

EnvComposer = Callable[..., dict[str, str]]
"""The signature of the noxfile's layered environment composers."""

HOST_INTERVAL_ENV = "OOK_LINKCHECK_HOST_INTERVAL"
"""Setting that spaces the link checker's requests to one host apart."""

TEST_ENV_COMPOSER = "_test_env_vars"
"""The noxfile helper that adds the pytest-only environment."""


def _session_decorator_stub(*args: Any, **kwargs: Any) -> Any:
    """Stand in for ``nox.session`` and ``nox_uv.session``.

    The noxfile applies both as ``@session(...)``; ``nox.session`` also
    supports a bare ``@session``.
    """
    if args and callable(args[0]):
        return args[0]

    def decorate(func: Any) -> Any:
        return func

    return decorate


@pytest.fixture(scope="module")
def noxfile() -> Iterator[Any]:
    """Load ``noxfile.py`` with the nox packages stubbed out."""
    nox_stub: Any = types.ModuleType("nox")
    nox_stub.options = types.SimpleNamespace()
    nox_stub.session = _session_decorator_stub
    nox_stub.Session = object
    nox_uv_stub: Any = types.ModuleType("nox_uv")
    nox_uv_stub.session = _session_decorator_stub

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setitem(sys.modules, "nox", nox_stub)
        monkeypatch.setitem(sys.modules, "nox_uv", nox_uv_stub)
        spec = importlib.util.spec_from_file_location(
            "ook_noxfile", _REPO_ROOT / "noxfile.py"
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module


@pytest.fixture(scope="module")
def xdist_args(noxfile: Any) -> PytestArgs:
    return noxfile._xdist_args


@pytest.fixture(scope="module")
def default_workers(noxfile: Any) -> Callable[[], int]:
    return noxfile._default_xdist_workers


@pytest.fixture(scope="module")
def unit_test_args(noxfile: Any) -> PytestArgs:
    return noxfile._unit_test_args


@pytest.fixture(scope="module")
def unit_test_env(noxfile: Any) -> SessionEnv:
    return noxfile._unit_test_env


@pytest.fixture(scope="module")
def base_env_vars(noxfile: Any) -> EnvComposer:
    return noxfile._make_env_vars


@pytest.fixture(scope="module")
def suite_env_vars(noxfile: Any) -> EnvComposer:
    return noxfile._test_env_vars


@pytest.fixture(scope="module")
def ignore_flags(noxfile: Any) -> list[str]:
    return [f"--ignore={path}" for path in noxfile.INFRA_TEST_PATHS]


@pytest.fixture(autouse=True)
def _repo_root_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve path posargs from the repository root.

    Nox runs the session from the directory holding the noxfile, so that is
    what a relative path posarg is resolved against.
    """
    monkeypatch.chdir(_REPO_ROOT)


@pytest.fixture(autouse=True)
def _four_available_cpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Detect four usable CPUs, whatever machine the suite runs on.

    The injected worker count is read off the host, so without this the
    ``-n 4`` the injection tests below assert would be whatever the machine
    at hand happens to have. Four is what a GitHub-hosted runner reports.
    The detection itself is covered by the tests that patch over this.
    """
    monkeypatch.setattr(os, "process_cpu_count", lambda: 4)


def test_the_default_run_is_parallel(xdist_args: PytestArgs) -> None:
    assert xdist_args([]) == ["-n", "4"]


def test_the_worker_count_follows_the_host(
    default_workers: Callable[[], int],
    xdist_args: PytestArgs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Below the cap, every usable CPU gets a worker.

    A hard-coded four left the count matching GitHub's runners alone: a
    developer machine with more cores ran the suite at a fraction of its
    width, and one with fewer oversubscribed itself.
    """
    monkeypatch.setattr(os, "process_cpu_count", lambda: 6)

    assert default_workers() == 6
    assert xdist_args([]) == ["-n", "6"]


def test_the_worker_count_stops_at_the_cap(
    noxfile: Any,
    default_workers: Callable[[], int],
    xdist_args: PytestArgs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wide host cannot buy more than the containers can feed.

    The workers run on the host, but the PostgreSQL and Kafka containers
    they drive share one small Docker VM, so past the cap the extra workers
    only queue up against the same database.
    """
    monkeypatch.setattr(os, "process_cpu_count", lambda: 64)
    cap = noxfile.XDIST_WORKER_CAP

    assert default_workers() == cap
    assert xdist_args([]) == ["-n", str(cap)]


def test_an_undetectable_cpu_count_runs_one_worker(
    default_workers: Callable[[], int],
    xdist_args: PytestArgs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``os.process_cpu_count`` may not know, and returns `None` if so.

    One worker is slow but correct; passing the `None` through would hand
    pytest-xdist a ``-n None`` it refuses, after the containers are up.
    """
    monkeypatch.setattr(os, "process_cpu_count", lambda: None)

    assert default_workers() == 1
    assert xdist_args([]) == ["-n", "1"]


@pytest.mark.parametrize(
    "posargs",
    [
        pytest.param(["-n", "2"], id="separate-value"),
        pytest.param(["-n2"], id="attached-value"),
        pytest.param(["-n", "0"], id="serial-run"),
        pytest.param(["-nauto"], id="attached-auto"),
        pytest.param(["--numprocesses", "2"], id="long-option-value"),
        pytest.param(["--numprocesses=2"], id="long-option-attached"),
        pytest.param(["-x", "-n0", "tests/domain"], id="among-other-posargs"),
    ],
)
def test_an_explicit_worker_count_wins(
    xdist_args: PytestArgs, posargs: list[str]
) -> None:
    assert xdist_args(posargs) == []


def test_an_unrelated_single_dash_option_stays_parallel(
    xdist_args: PytestArgs,
) -> None:
    """Only the ``-n`` spellings are worker counts, not every ``-n*`` arg.

    Matching the bare ``-n`` prefix silently dropped the suite to one
    process for any single-dash option that happens to start with an ``n``.
    """
    assert xdist_args(["-noflag"]) == ["-n", "4"]


@pytest.mark.parametrize(
    "posargs",
    [
        pytest.param(["--pdb"], id="pdb"),
        pytest.param(["--trace"], id="trace"),
        pytest.param(["-s"], id="output-passthrough"),
        pytest.param(["--capture=no"], id="capture-no"),
        pytest.param(["--capture", "no"], id="capture-no-separate-value"),
        pytest.param(
            ["--pdb", "tests/config_test.py"], id="alongside-a-selection"
        ),
    ],
)
def test_debugging_posargs_run_single_process(
    xdist_args: PytestArgs, posargs: list[str]
) -> None:
    """Workers defeat the debugger and swallow passthrough output.

    Injecting them anyway made ``nox -s test -- --pdb`` start both
    containers and only then die on pytest-xdist's "--pdb is incompatible
    with distributing tests", with nothing naming the noxfile as the source
    of the ``-n``. ``--trace`` reaches the same end by a quieter route:
    xdist's refusal only reads the option ``--pdb`` sets, so the workers are
    created and then each dies as pdb reads EOF from a stdin nobody can type
    into, failing every test it was given as a crash.
    """
    assert xdist_args(posargs) == []


@pytest.mark.parametrize(
    "posargs",
    [
        pytest.param(["--pdbcls=pdb:Pdb"], id="pdbcls-attached"),
        pytest.param(["--pdbcls", "pdb:Pdb"], id="pdbcls-separate-value"),
        pytest.param(["--trace-config"], id="trace-config"),
    ],
)
def test_debugger_adjacent_posargs_stay_parallel(
    xdist_args: PytestArgs, posargs: list[str]
) -> None:
    """Naming a debugger is not the same as starting one.

    ``--pdbcls`` only picks the class ``--pdb``, ``--trace``, or
    ``pytest.set_trace()`` would instantiate, and ``--trace-config`` traces
    conftest loading rather than test execution. Probing pytest-xdist 3.8.0
    ran both green under ``-n 2``, so dropping them to one process would
    cost the suite its parallelism for nothing.
    """
    assert xdist_args(posargs) == ["-n", "4"]


def test_other_capture_modes_stay_parallel(xdist_args: PytestArgs) -> None:
    """Only ``--capture=no`` needs the single process.

    The ``fd`` and ``sys`` modes capture per worker just fine.
    """
    assert xdist_args(["--capture=fd"]) == ["-n", "4"]


def test_bare_session_runs_the_unit_selection(
    unit_test_args: PytestArgs, ignore_flags: list[str]
) -> None:
    assert unit_test_args([]) == ["tests", *ignore_flags]


def test_option_posargs_keep_the_unit_selection(
    unit_test_args: PytestArgs, ignore_flags: list[str]
) -> None:
    """``-x`` must not cost the ignore list.

    Substituting posargs for the whole default selection used to collect the
    container-backed suite against the unreachable placeholder servers.
    """
    assert unit_test_args(["-x"]) == ["tests", "-x", *ignore_flags]


@pytest.mark.parametrize(
    "posargs",
    [
        pytest.param(["-k", "config"], id="keyword-expression"),
        pytest.param(["--maxfail", "3"], id="max-failures"),
        pytest.param(["-n", "0"], id="serial-run"),
    ],
)
def test_an_option_value_is_not_a_path_selection(
    unit_test_args: PytestArgs,
    ignore_flags: list[str],
    posargs: list[str],
) -> None:
    assert unit_test_args(posargs) == ["tests", *posargs, *ignore_flags]


def test_an_unknown_option_value_is_not_a_path_selection(
    unit_test_args: PytestArgs, ignore_flags: list[str]
) -> None:
    """A selection is a path under ``tests/``, not any path that exists.

    The separate-value option list is hand-curated, so an option missing
    from it -- ``--cov-config`` here, and whatever pytest or its plugins
    grow next -- had its value read as a selection whenever that value
    happened to name something on disk, quietly standing in for the whole
    unit selection.
    """
    posargs = ["--cov-config", "pyproject.toml"]

    assert unit_test_args(posargs) == ["tests", *posargs, *ignore_flags]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("tests", id="tests-root"),
        pytest.param("tests/services", id="under-an-infrastructure-path"),
    ],
)
def test_a_working_directory_option_is_not_a_path_selection(
    unit_test_args: PytestArgs, ignore_flags: list[str], value: str
) -> None:
    """``--basetemp`` says where pytest works, not what it collects.

    Its value does live under ``tests/``, so knowing the option is the only
    thing that keeps it out of the selection: ``--basetemp tests`` stood in
    for the default selection, and ``--basetemp tests/services`` was refused
    as a request for the container-backed suite.
    """
    posargs = ["--basetemp", value]

    assert unit_test_args(posargs) == ["tests", *posargs, *ignore_flags]


@pytest.mark.parametrize(
    "posargs",
    [
        pytest.param(["-n", "2"], id="separate-value"),
        pytest.param(["-n2"], id="attached-value"),
        pytest.param(["-n", "auto"], id="auto"),
        pytest.param(["--numprocesses=4"], id="long-option"),
    ],
)
def test_asking_for_xdist_workers_is_refused(
    unit_test_args: PytestArgs, posargs: list[str]
) -> None:
    """Workers would each want a database this session never starts.

    Without this the workers all crash importing the conftest shim, and the
    run reports "no tests ran" rather than why.
    """
    with pytest.raises(ValueError, match="pytest-xdist"):
        unit_test_args(posargs)


def test_an_unrelated_single_dash_option_is_not_a_worker_request(
    unit_test_args: PytestArgs, ignore_flags: list[str]
) -> None:
    """The refusal keys on the ``-n`` spellings, not every ``-n*`` arg."""
    assert unit_test_args(["-noflag"]) == ["tests", "-noflag", *ignore_flags]


def test_a_path_posarg_replaces_the_default_selection(
    unit_test_args: PytestArgs, ignore_flags: list[str]
) -> None:
    assert unit_test_args(["tests/config_test.py"]) == [
        "tests/config_test.py",
        *ignore_flags,
    ]


def test_a_node_id_posarg_replaces_the_default_selection(
    unit_test_args: PytestArgs, ignore_flags: list[str]
) -> None:
    node_id = "tests/config_test.py::test_something"

    assert unit_test_args([node_id]) == [node_id, *ignore_flags]


def test_a_path_posarg_mixes_with_options(
    unit_test_args: PytestArgs, ignore_flags: list[str]
) -> None:
    assert unit_test_args(["-x", "tests/domain"]) == [
        "-x",
        "tests/domain",
        *ignore_flags,
    ]


def test_selecting_the_tests_directory_keeps_the_ignore_list(
    unit_test_args: PytestArgs, ignore_flags: list[str]
) -> None:
    assert unit_test_args(["tests"]) == ["tests", *ignore_flags]


@pytest.mark.parametrize(
    "selection",
    [
        pytest.param("tests/services", id="directory"),
        pytest.param(
            "tests/handlers/authors/authors_endpoints_test.py", id="file"
        ),
        pytest.param("tests/dbschema_test.py::test_something", id="node-id"),
    ],
)
def test_selecting_an_infrastructure_path_is_refused(
    unit_test_args: PytestArgs, selection: str
) -> None:
    """A path this session ignores must fail loudly, not collect nothing."""
    with pytest.raises(ValueError, match="nox -s test") as excinfo:
        unit_test_args([selection])

    assert selection in str(excinfo.value)


def test_an_absolute_infrastructure_path_is_refused(
    unit_test_args: PytestArgs,
) -> None:
    selection = str(_REPO_ROOT / "tests" / "services")

    with pytest.raises(ValueError, match="nox -s test"):
        unit_test_args([selection])


def test_the_unit_session_marks_itself(unit_test_env: SessionEnv) -> None:
    """The session sets the marker the test-suite guard reads.

    The variable name is the whole interface between ``noxfile.py`` and
    `tests.support.unitsession`, and nothing else would notice if the two
    drifted apart: the guard would simply never fire, and a misclassified
    test would go back to failing on a connection error naming nothing.
    """
    assert unit_test_env()[UNIT_SESSION_ENV] == "1"


def test_the_unit_session_keeps_the_placeholder_servers(
    noxfile: Any, unit_test_env: SessionEnv
) -> None:
    """The unreachable servers stay as the backstop behind the guard.

    The guard covers the fixtures and the database-provisioning helper. Any
    other route to infrastructure still has to find nothing at the other end,
    rather than a developer's own PostgreSQL or Kafka on the default ports.
    """
    env = unit_test_env()

    assert env["OOK_DATABASE_URL"] == noxfile.UNREACHABLE_DATABASE_URL
    assert (
        env["KAFKA_BOOTSTRAP_SERVERS"] == noxfile.UNREACHABLE_KAFKA_BOOTSTRAP
    )


def test_the_base_environment_keeps_the_politeness_delay(
    base_env_vars: EnvComposer,
) -> None:
    """Sessions that reach real hosts get the application's own default.

    ``nox -s run`` and ``nox -s cli`` check links on the live internet, with
    real credentials in the CLI's case. Turning the per-host delay off there
    is exactly the behavior the link checker's Cloudflare bot-block handling
    exists to avoid, so the setting must not live in the shared base.
    """
    assert HOST_INTERVAL_ENV not in base_env_vars()


def test_the_test_environment_drops_the_politeness_delay(
    suite_env_vars: EnvComposer,
) -> None:
    """The whole pytest session shares one UrlChecker.

    Its host schedule would otherwise space every check of example.com a
    second apart across the entire suite -- a delay the per-test Kafka drain
    barrier would then have to wait out.
    """
    assert suite_env_vars()[HOST_INTERVAL_ENV] == "0s"


def test_the_unit_session_drops_the_politeness_delay(
    unit_test_env: SessionEnv,
) -> None:
    """The containers-free session composes the test environment too."""
    assert unit_test_env()[HOST_INTERVAL_ENV] == "0s"


@pytest.fixture(scope="module")
def noxfile_tree() -> ast.Module:
    """Parse ``noxfile.py`` for the static check below."""
    return ast.parse((_REPO_ROOT / "noxfile.py").read_text())


def _session_names(tree: ast.Module) -> set[str]:
    """Return the name of every nox session the noxfile defines.

    A session is a module-level function taking a ``nox.Session``. Reading
    them off the source this way means a session added later is covered
    without anyone remembering to list it here.
    """
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and any(
            argument.annotation is not None
            and ast.unparse(argument.annotation) == "nox.Session"
            for argument in node.args.args
        )
    }


def _call_graph(tree: ast.Module) -> dict[str, set[str]]:
    """Map each module-level function to the bare names it calls."""
    return {
        node.name: {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


def _calls_transitively(
    graph: Mapping[str, set[str]], start: str, target: str
) -> bool:
    """Report whether ``start`` reaches ``target`` through module calls."""
    seen: set[str] = set()
    pending = [start]
    while pending:
        name = pending.pop()
        if name == target:
            return True
        if name in seen:
            continue
        seen.add(name)
        pending.extend(graph.get(name, ()))
    return False


def test_only_the_test_sessions_compose_the_test_environment(
    noxfile_tree: ast.Module,
) -> None:
    """Pin the sessions the pytest-only environment reaches.

    Which sessions carry it is the whole point of splitting the environment
    in two, and nothing about `_test_env_vars` stops a future session from
    calling it. The delay was in the shared base once, which silently gave
    ``run`` and ``cli`` an impolite link checker.
    """
    graph = _call_graph(noxfile_tree)

    composing = {
        name
        for name in _session_names(noxfile_tree)
        if _calls_transitively(graph, name, TEST_ENV_COMPOSER)
    }

    assert composing == {"test", "test_coverage", "test_unit"}
