"""Tests for parsing Sphinx object inventories into domain entities."""

from __future__ import annotations

from pathlib import Path

import pytest
from sphobjinv import compress

from ook.domain.intersphinxentities import (
    PYTHON_SPHINX_DOMAIN,
    InventoryEntity,
    InventoryObject,
    PythonHierarchy,
    build_entities,
    parse_inventory,
)
from ook.exceptions import InventoryParseError

_SYNTHETIC_INVENTORY_LINES = [
    "# Sphinx inventory version 2",
    "# Project: Example",
    "# Version: 1.0",
    "# The remainder of this file is compressed using zlib.",
    # A module whose own parent ("example") is absent.
    "example.pkg py:module 0 api.html#module-$ -",
    # A class under a module that is present.
    "example.pkg.Thing py:class 1 api.html#$ -",
    # A method under a class that is present.
    "example.pkg.Thing.method py:method 1 api.html#$ -",
    # A class under a module that is absent from this inventory.
    "orphan.pkg.Widget py:class 1 widgets.html#$ Widget",
    # An unabbreviated URI and display name.
    "example.pkg.func py:function 1 api.html#example-func A function",
    # Non-py domains, which Ook does not model.
    "index std:doc -1 index.html Home",
    "some-label std:label -1 index.html#some-label Some Label",
    "",
]

SYNTHETIC_INVENTORY = "\n".join(_SYNTHETIC_INVENTORY_LINES)


def by_name[T: InventoryObject | InventoryEntity](
    records: list[T],
) -> dict[str, T]:
    """Index parsed objects or built entities by their name."""
    return {record.name: record for record in records}


@pytest.fixture(scope="session")
def synthetic_inventory() -> bytes:
    """Build a hand-written ``objects.inv`` payload covering the
    parser's edges.
    """
    return compress(SYNTHETIC_INVENTORY.encode("utf-8"))


@pytest.fixture
def synthetic_objects(
    synthetic_inventory: bytes,
) -> dict[str, InventoryObject]:
    """Parse every object the synthetic inventory declares, keyed by
    name.
    """
    return by_name(parse_inventory(synthetic_inventory))


@pytest.fixture
def synthetic_entities(
    synthetic_inventory: bytes,
) -> dict[str, InventoryEntity]:
    """Build the synthetic inventory's modelled entities, keyed by
    name.
    """
    return by_name(build_entities(parse_inventory(synthetic_inventory)))


@pytest.fixture(scope="session")
def pipelines_inventory() -> bytes:
    """Read the real ``objects.inv`` published by pipelines.lsst.io."""
    path = (
        Path(__file__).parent.parent
        / "data"
        / "intersphinx"
        / "pipelines.lsst.io.objects.inv"
    )
    return path.read_bytes()


def test_parse_inventory_reads_every_declared_object(
    synthetic_inventory: bytes,
) -> None:
    objects = parse_inventory(synthetic_inventory)

    # Parsing is faithful to the inventory: no domain is filtered here, and
    # the inventory's own order is kept.
    assert [obj.name for obj in objects] == [
        "example.pkg",
        "example.pkg.Thing",
        "example.pkg.Thing.method",
        "orphan.pkg.Widget",
        "example.pkg.func",
        "index",
        "some-label",
    ]
    assert {obj.sphinx_domain for obj in objects} == {"py", "std"}


def test_parse_inventory_records_domain_and_role(
    synthetic_objects: dict[str, InventoryObject],
) -> None:
    assert synthetic_objects["example.pkg"].sphinx_domain == "py"
    assert synthetic_objects["example.pkg"].role == "module"
    assert synthetic_objects["example.pkg.Thing"].role == "class"
    assert synthetic_objects["example.pkg.Thing.method"].role == "method"
    assert synthetic_objects["index"].sphinx_domain == "std"
    assert synthetic_objects["index"].role == "doc"


def test_dollar_suffix_uris_are_expanded(
    synthetic_objects: dict[str, InventoryObject],
) -> None:
    assert (
        synthetic_objects["example.pkg"].uri == "api.html#module-example.pkg"
    )
    assert (
        synthetic_objects["example.pkg.Thing"].uri
        == "api.html#example.pkg.Thing"
    )
    assert (
        synthetic_objects["example.pkg.Thing.method"].uri
        == "api.html#example.pkg.Thing.method"
    )


def test_uris_without_the_abbreviation_are_untouched(
    synthetic_objects: dict[str, InventoryObject],
) -> None:
    assert synthetic_objects["example.pkg.func"].uri == "api.html#example-func"


def test_abbreviated_dispnames_are_expanded(
    synthetic_objects: dict[str, InventoryObject],
) -> None:
    assert synthetic_objects["example.pkg.Thing"].dispname == (
        "example.pkg.Thing"
    )


def test_explicit_dispnames_are_preserved(
    synthetic_objects: dict[str, InventoryObject],
) -> None:
    assert synthetic_objects["orphan.pkg.Widget"].dispname == "Widget"
    assert synthetic_objects["example.pkg.func"].dispname == "A function"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"not an inventory at all", id="not-an-inventory"),
        pytest.param(
            b"# Sphinx inventory version 2\nbroken", id="corrupt-body"
        ),
    ],
)
def test_unparseable_payloads_raise_inventory_parse_error(
    payload: bytes,
) -> None:
    """Every way a payload can fail to parse surfaces as the one error.

    The corrupt-body case is why the parser's catch is broad: sphobjinv
    lets a bare AttributeError out of its line parser there.
    """
    with pytest.raises(InventoryParseError):
        parse_inventory(payload)


def test_python_hierarchy_splits_dotted_names() -> None:
    hierarchy = PythonHierarchy()

    assert (
        hierarchy.parent_name("lsst.afw.table.SourceCatalog")
        == "lsst.afw.table"
    )
    assert hierarchy.parent_name("lsst.afw") == "lsst"


def test_python_hierarchy_reports_no_parent_for_an_undotted_name() -> None:
    assert PythonHierarchy().parent_name("lsst") is None


def test_python_hierarchy_reports_no_parent_for_an_empty_prefix() -> None:
    """A leading dot leaves an empty prefix, which names nothing."""
    assert PythonHierarchy().parent_name(".lsst") is None


def test_build_entities_drops_domains_ook_does_not_model(
    synthetic_entities: dict[str, InventoryEntity],
) -> None:
    assert {
        entity.sphinx_domain for entity in synthetic_entities.values()
    } == {PYTHON_SPHINX_DOMAIN}
    assert "index" not in synthetic_entities
    assert "some-label" not in synthetic_entities


def test_build_entities_preserves_the_inventory_order(
    synthetic_inventory: bytes,
) -> None:
    entities = build_entities(parse_inventory(synthetic_inventory))

    assert [entity.name for entity in entities] == [
        "example.pkg",
        "example.pkg.Thing",
        "example.pkg.Thing.method",
        "orphan.pkg.Widget",
        "example.pkg.func",
    ]


def test_build_entities_links_a_documented_parent(
    synthetic_entities: dict[str, InventoryEntity],
) -> None:
    assert synthetic_entities["example.pkg.Thing"].parent_name == "example.pkg"
    assert synthetic_entities["example.pkg.Thing.method"].parent_name == (
        "example.pkg.Thing"
    )
    assert synthetic_entities["example.pkg.func"].parent_name == "example.pkg"


def test_build_entities_makes_a_missing_parent_top_level(
    synthetic_entities: dict[str, InventoryEntity],
) -> None:
    """A class whose module is absent is a top-level entity, not an error."""
    assert synthetic_entities["orphan.pkg.Widget"].parent_name is None
    assert synthetic_entities["example.pkg"].parent_name is None


def test_build_entities_carries_the_parsed_object_through(
    synthetic_entities: dict[str, InventoryEntity],
) -> None:
    assert synthetic_entities["example.pkg.Thing"] == InventoryEntity(
        sphinx_domain="py",
        role="class",
        name="example.pkg.Thing",
        dispname="example.pkg.Thing",
        uri="api.html#example.pkg.Thing",
        parent_name="example.pkg",
    )


def test_build_entities_ignores_a_parent_in_another_sphinx_domain() -> None:
    """A name is only a parent when it is documented in the same domain."""
    objects = [
        InventoryObject(
            sphinx_domain="std",
            role="doc",
            name="example.pkg",
            dispname="Example",
            uri="index.html",
        ),
        InventoryObject(
            sphinx_domain="py",
            role="class",
            name="example.pkg.Thing",
            dispname="example.pkg.Thing",
            uri="api.html#example.pkg.Thing",
        ),
    ]

    entities = build_entities(objects)

    assert [entity.name for entity in entities] == ["example.pkg.Thing"]
    assert entities[0].parent_name is None


def test_parses_the_real_pipelines_inventory(
    pipelines_inventory: bytes,
) -> None:
    objects = by_name(parse_inventory(pipelines_inventory))

    source_catalog = objects["lsst.afw.table.SourceCatalog"]
    assert source_catalog.sphinx_domain == "py"
    assert source_catalog.role == "class"
    assert source_catalog.dispname == "lsst.afw.table.SourceCatalog"
    assert source_catalog.uri == (
        "api/lsst.afw.table.SourceCatalog.html#lsst.afw.table.SourceCatalog"
    )
    # The real inventory carries std-domain entries too.
    assert {obj.sphinx_domain for obj in objects.values()} == {"py", "std"}


def test_builds_python_entities_from_the_real_pipelines_inventory(
    pipelines_inventory: bytes,
) -> None:
    entities = build_entities(parse_inventory(pipelines_inventory))

    assert {entity.sphinx_domain for entity in entities} == {
        PYTHON_SPHINX_DOMAIN
    }

    entities_by_name = by_name(entities)
    # lsst.afw.display is documented as a py:module, so its class hangs off
    # it, and the class's method hangs off the class.
    assert entities_by_name["lsst.afw.display"].role == "module"
    assert entities_by_name["lsst.afw.display.AsinhMapping"].parent_name == (
        "lsst.afw.display"
    )
    assert (
        entities_by_name[
            "lsst.afw.display.AsinhMapping.mapIntensityToUint8"
        ].parent_name
        == "lsst.afw.display.AsinhMapping"
    )


def test_a_parent_documented_only_outside_the_py_domain_is_not_linked(
    pipelines_inventory: bytes,
) -> None:
    """Real inventories carry this case, and it must not fabricate a parent.

    pipelines.lsst.io documents ``lsst.afw.table`` only as a ``std:label``
    for its module page, never as a ``py:module``. Its classes therefore
    have no parent in the ``py`` domain and come back top level, rather
    than pointing at an entity the python link domain will never hold.
    """
    objects = parse_inventory(pipelines_inventory)
    assert any(
        obj.sphinx_domain == "std" and obj.name == "lsst.afw.table"
        for obj in objects
    )

    entities_by_name = by_name(build_entities(objects))

    assert "lsst.afw.table" not in entities_by_name
    assert entities_by_name["lsst.afw.table.SourceCatalog"].parent_name is None
