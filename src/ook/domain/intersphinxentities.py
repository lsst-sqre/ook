"""Domain models for entities parsed out of Sphinx object inventories.

A Sphinx site publishes an ``objects.inv`` inventory listing every object
its documentation defines: the Sphinx domain and role that object was
declared with (``py:class``, ``std:label``, ...), its fully qualified
name, the display name to show for it, and the site-relative URI of the
page anchor documenting it. Ook turns those entries into the entities
behind the Links API's per-language domains.

Two models on the way in, because the inventory and the entity are not the
same thing. `InventoryObject` is a faithful reading of one inventory row
and knows nothing beyond it. `InventoryEntity` is that row placed in its
Sphinx domain's name hierarchy, which is not a property of the row at all:
it is decided by looking at the rest of the inventory, and the same row
yields a different parent depending on what else the site documents.

`IntersphinxEntityLinks` is the model on the way back out -- one stored
entity with the links every source contributed for it, which is the shape
the question "where is this object documented?" is answered in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from sphobjinv import Inventory

from ..exceptions import InventoryParseError
from .links import Link

__all__ = [
    "PYTHON_SPHINX_DOMAIN",
    "SPHINX_DOMAIN_HIERARCHIES",
    "IntersphinxEntityLinks",
    "IntersphinxSourceLink",
    "InventoryEntity",
    "InventoryObject",
    "PythonHierarchy",
    "SphinxDomainHierarchy",
    "build_entities",
    "parse_inventory",
]


PYTHON_SPHINX_DOMAIN = "py"
"""The Sphinx domain name for Python objects, which backs the Links API's
``python`` link domain.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class InventoryObject:
    """One object entry, exactly as an ``objects.inv`` inventory declares it.

    Both abbreviations the inventory format allows are already expanded
    here, so nothing downstream has to know they exist.
    """

    sphinx_domain: str
    """The Sphinx domain the object was declared in (``py``, ``std``, ...).

    This is the domain half of a cross-reference role like ``py:class``,
    not a hostname.
    """

    role: str
    """The Sphinx role within that domain (``class``, ``method``, ...)."""

    name: str
    """The object's fully qualified name, which is what a cross-reference
    targets.
    """

    dispname: str
    """The name to display for the object.

    Sphinx abbreviates this to ``-`` when it equals `name`; that
    abbreviation is expanded, so this is always a real display name.
    """

    uri: str
    """The object's URI, relative to the directory holding the inventory.

    Sphinx abbreviates a URI ending in the object's own name to a trailing
    ``$``; that abbreviation is expanded, so this is always a full anchor.
    Joining it to the documentation site's base URL is the caller's job --
    the inventory does not record where it was served from.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class InventoryEntity:
    """An inventory object placed in its Sphinx domain's name hierarchy."""

    sphinx_domain: str
    """The Sphinx domain the object was declared in."""

    role: str
    """The Sphinx role within that domain."""

    name: str
    """The object's fully qualified name."""

    dispname: str
    """The name to display for the object."""

    uri: str
    """The object's URI, relative to the directory holding the inventory."""

    parent_name: str | None
    """The name of the entity that contains this one, or None when it has
    none in this inventory.

    None covers both kinds of top-level entity, and deliberately does not
    distinguish them: a name the domain's hierarchy says has no parent at
    all (a root package), and a name whose parent the inventory simply does
    not document (a class whose module is undocumented). The second is
    common and is not an error -- a site is free to document a class
    without documenting its module -- and treating it as one would fail an
    ingest over a gap in someone else's documentation.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class IntersphinxEntityLinks:
    """A stored entity together with the documentation links to it.

    An entity is stored once per ``(sphinx_domain, name)`` however many
    sites document it, so `links` is the union across sources rather than
    one site's view. It is empty for an entity that exists only to hold
    documented descendants -- a package whose own page no source publishes.
    """

    sphinx_domain: str
    """The Sphinx domain the entity was declared in."""

    name: str
    """The entity's fully qualified name within that domain."""

    role: str
    """The Sphinx role the entity was declared with."""

    dispname: str
    """The name to display for the entity."""

    parent_name: str | None
    """The name of the entity that contains this one, or None when it is
    top level.

    The name rather than the ID, because the ID is a storage detail and the
    name is what a caller can look the parent up by.
    """

    extras: dict[str, Any] | None
    """Domain-specific attributes with no field of their own, or None."""

    links: list[Link] = field(default_factory=list)
    """The documentation links to this entity, from every source that
    documents it.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class IntersphinxSourceLink:
    """One documentation link that one source contributes for one entity.

    The write-side counterpart of `IntersphinxEntityLinks`: that model is
    an entity with every source's links, while this is one source's claim
    about one entity, which is the unit a per-source replace writes.

    The source itself is not named here. Every link in a replace comes from
    the same source, so the source's ID and its title travel with the call
    rather than being repeated on each of a site's tens of thousands of
    links.
    """

    entity_id: int
    """The database ID of the entity this link documents."""

    html_url: str
    """The absolute URL of the page anchor documenting the entity.

    Absolute, unlike `InventoryEntity.uri`: the inventory records a URI
    relative to itself, and resolving it against the site the inventory was
    served from is the ingest service's job.
    """

    title: str
    """The title of the documentation this link points at."""

    type: str
    """The kind of documentation this link points at (``python_api``)."""


class SphinxDomainHierarchy(Protocol):
    """A strategy for reading containment out of one Sphinx domain's names.

    Sphinx domains name their objects to their own conventions, and the
    containment those names imply is domain-specific: Python's dotted
    names nest, while a ``std`` label's name says nothing about what
    contains it. Each domain Ook models therefore brings its own strategy,
    and `SPHINX_DOMAIN_HIERARCHIES` is the set of domains that have one.
    """

    def parent_name(self, name: str) -> str | None:
        """Name the entity that would contain *name* in this domain.

        This answers from the name alone. Whether the inventory actually
        documents that parent is `build_entities`' question, not this
        one's.

        Parameters
        ----------
        name
            The fully qualified name of an object in this domain.

        Returns
        -------
        str or None
            The containing entity's name, or None when the domain's naming
            convention puts this name at the top level.
        """
        ...


@dataclass(frozen=True, slots=True)
class PythonHierarchy:
    """Containment for the ``py`` domain, read off dotted names.

    A Python object's qualified name spells out its own containment:
    ``lsst.afw.table.SourceCatalog`` is in ``lsst.afw.table``, and
    ``lsst.afw.table.SourceCatalog.find`` is in the class. Splitting at the
    last dot therefore recovers the parent without needing the roles
    involved, which matters because the inventory's role vocabulary does
    not line up with containment: an ``attribute`` sits inside a ``class``
    or a ``module`` indifferently, and ``module`` objects nest in each
    other.
    """

    def parent_name(self, name: str) -> str | None:
        """Return everything before the last dot in *name*.

        Parameters
        ----------
        name
            A fully qualified Python object name.

        Returns
        -------
        str or None
            The dotted prefix, or None when *name* has no dot before a
            non-empty prefix.
        """
        prefix, separator, _ = name.rpartition(".")
        if not separator:
            return None
        # A leading dot leaves an empty prefix, which names nothing.
        return prefix or None


SPHINX_DOMAIN_HIERARCHIES: Mapping[str, SphinxDomainHierarchy] = (
    MappingProxyType({PYTHON_SPHINX_DOMAIN: PythonHierarchy()})
)
"""The Sphinx domains Ook models, each with the strategy that reads its
hierarchy.

This is one mapping rather than a filter plus a lookup because the two
questions have the same answer: Ook can store a domain's objects exactly
when it knows how to place them in a hierarchy. Adding a domain is adding
an entry.
"""


def parse_inventory(content: bytes) -> list[InventoryObject]:
    """Parse an ``objects.inv`` payload into the objects it declares.

    Every object is returned, in inventory order and whatever its Sphinx
    domain; selecting the domains Ook models is `build_entities`' job.

    Parameters
    ----------
    content
        The raw, zlib-compressed bytes of a version 2 ``objects.inv`` file.

    Returns
    -------
    list of InventoryObject
        The inventory's objects, in the order it lists them.

    Raises
    ------
    InventoryParseError
        Raised if the payload is not a readable version 2 inventory.
    """
    try:
        inventory = Inventory(zlib=content)
    except Exception as e:
        # Deliberately broad: sphobjinv reports a payload that is not an
        # inventory as its own VersionError, but a payload with an
        # inventory header and a corrupt body escapes as a bare
        # AttributeError from its line parser. An allowlist of exception
        # types would let that one through to abort a whole ingest run over
        # a single site's bad bytes.
        raise InventoryParseError(
            f"Could not parse a Sphinx objects.inv payload: {e}"
        ) from e

    return [
        InventoryObject(
            sphinx_domain=obj.domain,
            role=obj.role,
            name=obj.name,
            dispname=obj.dispname_expanded,
            uri=obj.uri_expanded,
        )
        for obj in inventory.objects
    ]


def build_entities(
    objects: Iterable[InventoryObject],
    *,
    hierarchies: Mapping[
        str, SphinxDomainHierarchy
    ] = SPHINX_DOMAIN_HIERARCHIES,
) -> list[InventoryEntity]:
    """Select the objects Ook models and resolve each one's parent.

    Objects in a Sphinx domain with no hierarchy strategy are dropped. A
    parent is linked only when the same inventory documents it *in the
    same Sphinx domain*, so an entity whose parent is missing comes back
    top level rather than pointing at a name nothing will resolve.

    Only the immediate parent is considered: if a class's module is
    undocumented, the class is top level rather than being reparented onto
    its grandparent package, which would advertise a containment the
    documentation does not have.

    Parameters
    ----------
    objects
        Objects parsed from one inventory, as `parse_inventory` returns
        them.
    hierarchies
        The Sphinx domains to keep, each mapped to the strategy that reads
        its hierarchy. Defaults to every domain Ook models.

    Returns
    -------
    list of InventoryEntity
        The kept objects, in the order they were given.
    """
    modelled = [obj for obj in objects if obj.sphinx_domain in hierarchies]

    documented: dict[str, set[str]] = {}
    for obj in modelled:
        documented.setdefault(obj.sphinx_domain, set()).add(obj.name)

    entities: list[InventoryEntity] = []
    for obj in modelled:
        parent_name = hierarchies[obj.sphinx_domain].parent_name(obj.name)
        if (
            parent_name is not None
            and parent_name not in documented[obj.sphinx_domain]
        ):
            parent_name = None
        entities.append(
            InventoryEntity(
                sphinx_domain=obj.sphinx_domain,
                role=obj.role,
                name=obj.name,
                dispname=obj.dispname,
                uri=obj.uri,
                parent_name=parent_name,
            )
        )
    return entities
