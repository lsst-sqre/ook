"""Tests for the arguments the ``test-unit`` nox session gives pytest.

That session runs the infrastructure-free part of the suite against
unreachable placeholder servers, which only holds while the ``--ignore``
flags for `INFRA_TEST_PATHS` survive whatever posargs a developer passes.
These tests load ``noxfile.py`` and pin that composition down.

Nox itself is not installed in the test environment -- it lives in the
``nox`` dependency group, which bootstraps the sessions from outside -- so
the two modules the noxfile imports for its decorators are stubbed before
it is loaded.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).parents[1]

UnitTestArgs = Callable[[Sequence[str]], list[str]]
"""The signature of the noxfile's argument composer."""


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
def unit_test_args(noxfile: Any) -> UnitTestArgs:
    return noxfile._unit_test_args


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


def test_bare_session_runs_the_unit_selection(
    unit_test_args: UnitTestArgs, ignore_flags: list[str]
) -> None:
    assert unit_test_args([]) == ["tests", *ignore_flags]


def test_option_posargs_keep_the_unit_selection(
    unit_test_args: UnitTestArgs, ignore_flags: list[str]
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
    unit_test_args: UnitTestArgs,
    ignore_flags: list[str],
    posargs: list[str],
) -> None:
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
    unit_test_args: UnitTestArgs, posargs: list[str]
) -> None:
    """Workers would each want a database this session never starts.

    Without this the workers all crash importing the conftest shim, and the
    run reports "no tests ran" rather than why.
    """
    with pytest.raises(ValueError, match="pytest-xdist"):
        unit_test_args(posargs)


def test_a_path_posarg_replaces_the_default_selection(
    unit_test_args: UnitTestArgs, ignore_flags: list[str]
) -> None:
    assert unit_test_args(["tests/config_test.py"]) == [
        "tests/config_test.py",
        *ignore_flags,
    ]


def test_a_node_id_posarg_replaces_the_default_selection(
    unit_test_args: UnitTestArgs, ignore_flags: list[str]
) -> None:
    node_id = "tests/config_test.py::test_something"

    assert unit_test_args([node_id]) == [node_id, *ignore_flags]


def test_a_path_posarg_mixes_with_options(
    unit_test_args: UnitTestArgs, ignore_flags: list[str]
) -> None:
    assert unit_test_args(["-x", "tests/domain"]) == [
        "-x",
        "tests/domain",
        *ignore_flags,
    ]


def test_selecting_the_tests_directory_keeps_the_ignore_list(
    unit_test_args: UnitTestArgs, ignore_flags: list[str]
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
    unit_test_args: UnitTestArgs, selection: str
) -> None:
    """A path this session ignores must fail loudly, not collect nothing."""
    with pytest.raises(ValueError, match="nox -s test") as excinfo:
        unit_test_args([selection])

    assert selection in str(excinfo.value)


def test_an_absolute_infrastructure_path_is_refused(
    unit_test_args: UnitTestArgs,
) -> None:
    selection = str(_REPO_ROOT / "tests" / "services")

    with pytest.raises(ValueError, match="nox -s test"):
        unit_test_args([selection])
