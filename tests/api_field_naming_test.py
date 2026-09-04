"""Guard the naming and typing conventions of Ook's HTTP API fields.

Two conventions govern the Pydantic models under
:file:`src/ook/handlers`, and this module fails the build when a handler
model breaks either one.

Ook names every date-valued field of its HTTP API with a ``date_``
prefix -- ``date_created``, ``date_checked``, ``date_next_check``.
Both timestamps and bare calendar dates are in scope.

Ook publishes every identifier of its HTTP API as a Crockford Base32 ID
from `ook.domain.base32id`, never as the database's integer primary key,
so an ``id`` or ``*_id`` field typed as a plain `int` is a bug.

Both conventions are written up in the style guide in
:file:`docs/dev/development.rst`; this is the test that keeps them true.

The test is pure introspection over the Pydantic models under
:file:`src/ook/handlers`, so it needs no database, no Kafka, and no
application fixture, and it runs in the containers-free unit tier.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable, Iterator
from datetime import date, datetime
from types import ModuleType
from typing import Annotated, Any, TypeAliasType, get_args, get_origin

from pydantic import BaseModel, Field

import ook.handlers
from ook.domain.base32id import Base32Id, Base32IdNoHyphens, Base32IdShort

DATE_PREFIX = "date_"
"""The prefix every date-valued API field name must carry."""


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
    surface both conventions govern. A model merely imported into a
    handler module is excluded, because the filter is on where the model
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


def _is_date_valued(annotation: Any) -> bool:
    """Report whether ``annotation`` can carry a `~datetime.date`.

    Unwraps the wrappers Ook's API models actually use -- ``Annotated[...]``
    metadata, ``X | None`` unions, and container generics such as
    ``list[...]`` -- so an optional or annotated timestamp is not silently
    skipped. Origins are tested before the bare-class case so a
    parameterized generic is never handed to `issubclass`.

    The check is against `~datetime.date` rather than
    `~datetime.datetime` because the convention governs calendar dates as
    well as timestamps. `~datetime.datetime` is a subclass of
    `~datetime.date`, so the single check covers both.
    """
    origin = get_origin(annotation)
    if origin is Annotated:
        return _is_date_valued(get_args(annotation)[0])
    if origin is not None:
        return any(_is_date_valued(arg) for arg in get_args(annotation))
    return isinstance(annotation, type) and issubclass(annotation, date)


def date_fields_missing_date_prefix(
    models: Iterable[type[BaseModel]],
) -> list[str]:
    """Return a label per date-valued field whose name lacks ``date_``.

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
            if _is_date_valued(field.annotation):
                violations.append(
                    f"{model.__module__}.{model.__name__}.{name}"
                )
    return sorted(violations)


def test_datetime_field_without_the_prefix_is_reported() -> None:
    class Offender(BaseModel):
        checked_at: datetime

    assert date_fields_missing_date_prefix([Offender]) == [
        f"{__name__}.Offender.checked_at"
    ]


def test_optional_and_annotated_datetimes_are_resolved() -> None:
    class Offender(BaseModel):
        failing_since: Annotated[
            datetime | None, Field(description="Nested in Annotated.")
        ] = None
        next_check_at: datetime | None = None
        observed_at: list[Annotated[datetime, Field()]] = []

    assert date_fields_missing_date_prefix([Offender]) == [
        f"{__name__}.Offender.failing_since",
        f"{__name__}.Offender.next_check_at",
        f"{__name__}.Offender.observed_at",
    ]


def test_bare_calendar_dates_are_reported() -> None:
    # A field holding a calendar date carries no time, but it is still a
    # date-valued field and the convention governs it. ``datetime``
    # subclasses ``date``, so the one check covers both.
    class Offender(BaseModel):
        published_on: date
        embargoed_until: date | None = None

    assert date_fields_missing_date_prefix([Offender]) == [
        f"{__name__}.Offender.embargoed_until",
        f"{__name__}.Offender.published_on",
    ]


def test_conforming_and_non_datetime_fields_are_not_reported() -> None:
    class Conforming(BaseModel):
        date_checked: Annotated[datetime | None, Field()] = None
        date_created: datetime
        # A field holding a verbatim HTTP header value mirrors the wire
        # name rather than Ook's convention, and is typed as the string it
        # is, so the guard never sees it.
        last_modified: str | None = None

    assert date_fields_missing_date_prefix([Conforming]) == []


def test_discovery_reaches_handler_models_with_timestamps() -> None:
    # Naming actual timestamp fields keeps the guard below from passing
    # vacuously, which is what it would do if discovery ever found nothing.
    dated_fields = {
        f"{model.__name__}.{name}"
        for model in handler_api_models()
        for name, field in model.model_fields.items()
        if _is_date_valued(field.annotation)
    }
    assert "UrlRecord.date_last_checked" in dated_fields
    assert "ResourceMetadata.date_created" in dated_fields
    assert "DocumentRequest.date_resource_published" in dated_fields


def test_handler_date_fields_use_the_date_prefix() -> None:
    violations = date_fields_missing_date_prefix(handler_api_models())
    listing = "\n".join(f"  - {violation}" for violation in violations)
    assert not violations, (
        "Ook names every date-valued API field with a date_ prefix"
        " (date_created, date_checked, date_next_check); see the style"
        f" guide in docs/dev/development.rst. Rename:\n{listing}"
    )


IDENTIFIER_ALIASES = (Base32Id, Base32IdNoHyphens, Base32IdShort)
"""The public identifier types an ``id`` or ``*_id`` API field may carry.

Each is a :pep:`695` ``type`` alias for an annotated `int`, so Pydantic
leaves it unexpanded on ``FieldInfo.annotation`` and reports no metadata
of its own. `_is_plain_int` therefore keys on the alias objects
themselves rather than on the field's metadata.
"""


def _is_identifier_name(name: str) -> bool:
    """Report whether ``name`` names an identifier field."""
    return name == "id" or name.endswith("_id")


def _is_identifier_alias(annotation: Any) -> bool:
    """Report whether ``annotation`` is one of `IDENTIFIER_ALIASES`."""
    return any(annotation is alias for alias in IDENTIFIER_ALIASES)


def _is_plain_int(annotation: Any) -> bool:
    """Report whether ``annotation`` resolves to a bare `int`.

    An identifier annotated with one of `IDENTIFIER_ALIASES` is an `int`
    at runtime, so the aliases are matched by identity before anything is
    unwrapped. Everything else is unwrapped the way `_is_date_valued`
    unwraps it -- ``Annotated[...]`` wrappers, ``X | None`` unions, and
    container generics -- with the addition that any other :pep:`695`
    ``type`` alias is resolved through its ``__value__``, so a local
    alias for a database integer is reported rather than excused.

    A `str` identifier, such as the authors' ``internal_id`` or the
    linkcheck contribution's GitHub ``run_id``, never resolves to `int`
    and is therefore allowed.
    """
    if _is_identifier_alias(annotation):
        return False
    if isinstance(annotation, TypeAliasType):
        return _is_plain_int(annotation.__value__)
    origin = get_origin(annotation)
    if origin is Annotated:
        return _is_plain_int(get_args(annotation)[0])
    if origin is not None:
        return any(_is_plain_int(arg) for arg in get_args(annotation))
    return annotation is int


def int_typed_identifier_fields(
    models: Iterable[type[BaseModel]],
) -> list[str]:
    """Return a label per identifier field typed as a plain `int`.

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
            if not _is_identifier_name(name):
                continue
            if _is_plain_int(field.annotation):
                violations.append(
                    f"{model.__module__}.{model.__name__}.{name}"
                )
    return sorted(violations)


def test_int_typed_identifiers_are_reported() -> None:
    class Offender(BaseModel):
        id: int
        parent_id: int | None = None
        source_id: Annotated[int, Field(description="Nested in Annotated.")]

    assert int_typed_identifier_fields([Offender]) == [
        f"{__name__}.Offender.id",
        f"{__name__}.Offender.parent_id",
        f"{__name__}.Offender.source_id",
    ]


def test_an_alias_for_a_database_integer_is_reported() -> None:
    # A local alias is not a public identifier type; resolving it through
    # __value__ keeps the guard from being sidestepped by a name.
    type EntityId = int

    class Offender(BaseModel):
        entity_id: EntityId

    assert int_typed_identifier_fields([Offender]) == [
        f"{__name__}.Offender.entity_id"
    ]


def test_base32_and_string_identifiers_are_not_reported() -> None:
    class Conforming(BaseModel):
        id: Annotated[Base32Id, Field(description="The record's ID.")]
        check_id: Base32Id | None = None
        packed_id: Base32IdNoHyphens
        short_id: Base32IdShort
        # An identifier that is genuinely a string upstream -- the authors'
        # internal_id, a GitHub Actions run_id -- keeps its own type.
        internal_id: str
        run_id: str | None = None
        # A plain int that does not name an identifier is out of scope.
        total_count: int = 0

    assert int_typed_identifier_fields([Conforming]) == []


def test_plain_int_resolver_unwraps_annotations_and_aliases() -> None:
    assert _is_plain_int(int)
    assert _is_plain_int(Annotated[int, Field()])
    assert _is_plain_int(int | None)
    assert not _is_plain_int(Base32Id)
    assert not _is_plain_int(Base32Id | None)
    assert not _is_plain_int(Annotated[Base32Id, Field()])
    assert not _is_plain_int(str)


def test_discovery_reaches_handler_models_with_base32_identifiers() -> None:
    # Naming actual Base32 identifier fields keeps the guard below from
    # passing vacuously, which is what it would do if discovery ever found
    # no identifier fields at all.
    base32_id_fields = {
        f"{model.__name__}.{name}"
        for model in handler_api_models()
        for name, field in model.model_fields.items()
        if _is_identifier_name(name) and _is_identifier_alias(field.annotation)
    }
    assert "LinkCheck.id" in base32_id_fields
    assert "IntersphinxSource.id" in base32_id_fields
    assert "ResourceSummaryAPI.id" in base32_id_fields


def test_handler_identifier_fields_are_base32_ids() -> None:
    violations = int_typed_identifier_fields(handler_api_models())
    listing = "\n".join(f"  - {violation}" for violation in violations)
    assert not violations, (
        "Every identifier in Ook's HTTP API is a Crockford Base32 ID from"
        " ook.domain.base32id, never the database's integer primary key;"
        f" see the style guide in docs/dev/development.rst. Retype:\n"
        f"{listing}"
    )
