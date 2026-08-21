"""Guard Ook's ``date_`` prefix convention for datetime-valued API fields.

Ook names every datetime-valued field of its HTTP API with a ``date_``
prefix -- ``date_created``, ``date_checked``, ``date_next_check`` -- and
this module fails the build when a handler model gains one that does not.
The convention is written up in the style guide in
:file:`docs/dev/development.rst`; this is the test that keeps it true.

The test is pure introspection over the Pydantic models under
:file:`src/ook/handlers`, so it needs no database, no Kafka, and no
application fixture, and it runs in the containers-free unit tier.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable, Iterator
from datetime import datetime
from types import ModuleType
from typing import Annotated, Any, get_args, get_origin

from pydantic import BaseModel, Field

import ook.handlers

DATE_PREFIX = "date_"
"""The prefix every datetime-valued API field name must carry."""


def _iter_handler_modules() -> Iterator[ModuleType]:
    """Yield `ook.handlers` and every module beneath it."""
    yield ook.handlers
    for module_info in pkgutil.walk_packages(
        ook.handlers.__path__, prefix=f"{ook.handlers.__name__}."
    ):
        yield importlib.import_module(module_info.name)


def _is_defined_in_handlers(model: type[BaseModel]) -> bool:
    """Report whether ``model`` is defined in the `ook.handlers` package."""
    package = ook.handlers.__name__
    return model.__module__ == package or model.__module__.startswith(
        f"{package}."
    )


def handler_api_models() -> list[type[BaseModel]]:
    """Return every Pydantic model defined under `ook.handlers`.

    These are Ook's HTTP request and response models -- the public API
    surface the ``date_`` convention governs. A model merely imported into
    a handler module is excluded, because the filter is on where the model
    is defined. That keeps `ook.domain`'s internal models and the Kafka
    message schemas, whose field names follow the event contract rather
    than Ook's HTTP API convention, out of scope.

    Returns
    -------
    list
        The discovered models, ordered by qualified name.
    """
    models: dict[str, type[BaseModel]] = {}
    for module in _iter_handler_modules():
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseModel)
                and _is_defined_in_handlers(obj)
            ):
                models[f"{obj.__module__}.{obj.__name__}"] = obj
    return [models[key] for key in sorted(models)]


def _is_datetime_valued(annotation: Any) -> bool:
    """Report whether ``annotation`` can carry a `~datetime.datetime`.

    Unwraps the wrappers Ook's API models actually use -- ``Annotated[...]``
    metadata, ``X | None`` unions, and container generics such as
    ``list[...]`` -- so an optional or annotated timestamp is not silently
    skipped. Origins are tested before the bare-class case so a
    parameterized generic is never handed to `issubclass`.
    """
    origin = get_origin(annotation)
    if origin is Annotated:
        return _is_datetime_valued(get_args(annotation)[0])
    if origin is not None:
        return any(_is_datetime_valued(arg) for arg in get_args(annotation))
    return isinstance(annotation, type) and issubclass(annotation, datetime)


def datetime_fields_missing_date_prefix(
    models: Iterable[type[BaseModel]],
) -> list[str]:
    """Return a label per datetime field whose name lacks ``date_``.

    Parameters
    ----------
    models
        The Pydantic models to inspect.

    Returns
    -------
    list
        Sorted ``module.Model.field`` labels, one per offending field.
    """
    violations: list[str] = []
    for model in models:
        for name, field in model.model_fields.items():
            if name.startswith(DATE_PREFIX):
                continue
            if _is_datetime_valued(field.annotation):
                violations.append(
                    f"{model.__module__}.{model.__name__}.{name}"
                )
    return sorted(violations)


def test_datetime_field_without_the_prefix_is_reported() -> None:
    class Offender(BaseModel):
        checked_at: datetime

    assert datetime_fields_missing_date_prefix([Offender]) == [
        f"{__name__}.Offender.checked_at"
    ]


def test_optional_and_annotated_datetimes_are_resolved() -> None:
    class Offender(BaseModel):
        failing_since: Annotated[
            datetime | None, Field(description="Nested in Annotated.")
        ] = None
        next_check_at: datetime | None = None
        observed_at: list[Annotated[datetime, Field()]] = []

    assert datetime_fields_missing_date_prefix([Offender]) == [
        f"{__name__}.Offender.failing_since",
        f"{__name__}.Offender.next_check_at",
        f"{__name__}.Offender.observed_at",
    ]


def test_conforming_and_non_datetime_fields_are_not_reported() -> None:
    class Conforming(BaseModel):
        date_checked: Annotated[datetime | None, Field()] = None
        date_created: datetime
        # A field holding a verbatim HTTP header value mirrors the wire
        # name rather than Ook's convention, and is typed as the string it
        # is, so the guard never sees it.
        last_modified: str | None = None

    assert datetime_fields_missing_date_prefix([Conforming]) == []


def test_discovery_reaches_handler_models_with_timestamps() -> None:
    # Naming actual timestamp fields keeps the guard below from passing
    # vacuously, which is what it would do if discovery ever found nothing.
    dated_fields = {
        f"{model.__name__}.{name}"
        for model in handler_api_models()
        for name, field in model.model_fields.items()
        if _is_datetime_valued(field.annotation)
    }
    assert "UrlRecord.date_last_checked" in dated_fields
    assert "ResourceMetadata.date_created" in dated_fields
    assert "DocumentRequest.date_resource_published" in dated_fields


def test_handler_datetime_fields_use_the_date_prefix() -> None:
    violations = datetime_fields_missing_date_prefix(handler_api_models())
    listing = "\n".join(f"  - {violation}" for violation in violations)
    assert not violations, (
        "Ook names every datetime-valued API field with a date_ prefix"
        " (date_created, date_checked, date_next_check); see the style"
        f" guide in docs/dev/development.rst. Rename:\n{listing}"
    )
