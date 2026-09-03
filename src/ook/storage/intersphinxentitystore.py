"""Storage interface for entities parsed out of Sphinx object inventories."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Self, cast, override

from safir.database import (
    CountedPaginatedList,
    CountedPaginatedQueryRunner,
    InvalidCursorError,
    PaginationCursor,
)
from sqlalchemy import (
    ColumnElement,
    CursorResult,
    Select,
    Table,
    case,
    delete,
    exists,
    func,
    insert,
    literal,
    null,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from structlog.stdlib import BoundLogger

from ook.dbschema.intersphinxentities import SqlIntersphinxEntity
from ook.dbschema.links import SqlIntersphinxLink, SqlLink
from ook.domain.intersphinxentities import (
    PYTHON_SPHINX_DOMAIN,
    IntersphinxEntityLinks,
    IntersphinxSourceLink,
    InventoryEntity,
)
from ook.domain.links import Link

__all__ = [
    "SPHINX_DOMAIN_PARENT_NAME_SQL",
    "IntersphinxEntityCursor",
    "IntersphinxEntityStore",
]


_UPSERT_CHUNK_SIZE = 1000
"""How many entity rows one ``INSERT`` statement carries.

A large site's inventory runs to tens of thousands of objects while
PostgreSQL caps a statement at 65535 bind parameters, so an unchunked
insert of a real inventory would fail on the protocol rather than on
anything about the data.
"""


_LINK_DISCRIMINATOR = cast(
    "str", SqlIntersphinxLink.__mapper__.polymorphic_identity
)
"""The value the base ``link`` table's discriminator carries for an
intersphinx link.

Read off the mapper rather than spelled again, because these inserts write
the base row themselves: a literal here would be a second declaration of
the subtype's identity, free to drift from the one the ORM reads rows back
by.
"""


_DOTTED_PARENT_PATTERN = r"^(.*)\.[^.]*$"
"""A POSIX pattern capturing everything before a name's last dot.

Greedy, so it splits at the *last* dot: ``lsst.afw.table.SourceCatalog``
captures ``lsst.afw.table``. A name with no dot matches nothing and the
extraction is null, and a name whose prefix is empty (a leading dot)
captures the empty string, which `_dotted_parent_name` maps to null too.
"""


def _dotted_parent_name(name: ColumnElement[str]) -> ColumnElement[str | None]:
    """Name the entity a dotted name sits inside, as a SQL expression.

    The SQL half of
    `~ook.domain.intersphinxentities.PythonHierarchy.parent_name`, which is
    the readable statement of the same rule. Two implementations because
    containment is *derived* over the whole table rather than decided row by
    row: pulling every stored name into Python to split it would be a round
    trip per recompute of a corpus that runs to tens of thousands of names.
    They are pinned to each other by test.
    """
    return func.nullif(
        func.substring(name, literal(_DOTTED_PARENT_PATTERN)), literal("")
    )


SPHINX_DOMAIN_PARENT_NAME_SQL: Mapping[
    str, Callable[[ColumnElement[str]], ColumnElement[str | None]]
] = MappingProxyType({PYTHON_SPHINX_DOMAIN: _dotted_parent_name})
"""How each Sphinx domain's containment reads in SQL, keyed by domain.

The recompute's scope as well as its rule: a row in a domain absent from
this mapping is never touched, which is what keeps a future entity kind
whose containment comes from somewhere other than its name -- SDM's, from
Felis -- out of the recompute's way. Its keys must match
`~ook.domain.intersphinxentities.SPHINX_DOMAIN_HIERARCHIES`, whose
strategies state the same rule in Python.
"""


class IntersphinxEntityStore:
    """An interface to stored Sphinx-domain entities and their links.

    Follows the store conventions of an ``AsyncSession`` plus a
    ``BoundLogger`` constructor with caller-managed transactions.
    """

    def __init__(self, session: AsyncSession, logger: BoundLogger) -> None:
        self._session = session
        self._logger = logger

    async def upsert_entities(
        self, entities: Sequence[InventoryEntity]
    ) -> dict[tuple[str, str], int]:
        """Insert or update the entities an inventory declares.

        Only the identity and the descriptive columns are written. Neither
        `InventoryEntity.parent_name` nor `InventoryEntity.uri` is read
        here, for the same kind of reason: the URI locates the object on
        one particular site, which makes it a property of that site's link
        rather than of the entity every site shares, and the parent one
        inventory proposes is not the stored relation either. Containment
        is derived from every source's links at once, by
        `recompute_containment`, which the caller runs after replacing the
        links this batch belongs to.

        Parameters
        ----------
        entities
            The entities to store. Duplicates of a
            ``(sphinx_domain, name)`` pair are merged, keeping the first --
            one inventory may declare a name under two roles, and neither
            declaration is more authoritative than the other, so the
            inventory's own order breaks the tie.

        Returns
        -------
        dict
            The database ID of every entity written, keyed by its
            ``(sphinx_domain, name)`` identity. This is what lets the
            caller write links without reading the rows back.
        """
        deduplicated = self._deduplicate(entities)
        if not deduplicated:
            return {}

        entity_ids = await self._upsert_rows(deduplicated)
        await self._session.flush()
        return entity_ids

    async def get_entity(
        self, sphinx_domain: str, name: str
    ) -> IntersphinxEntityLinks | None:
        """Get one entity with the links to it from every source.

        Parameters
        ----------
        sphinx_domain
            The Sphinx domain the entity was declared in.
        name
            The entity's fully qualified name within that domain.

        Returns
        -------
        IntersphinxEntityLinks or None
            The entity and its links, or None if the pair names no stored
            entity.
        """
        stmt = self._entity_select().where(
            SqlIntersphinxEntity.sphinx_domain == sphinx_domain,
            SqlIntersphinxEntity.name == name,
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None

        return IntersphinxEntityLinks(
            sphinx_domain=row.sphinx_domain,
            name=row.name,
            role=row.role,
            display_name=row.display_name,
            parent_name=row.parent_name,
            extras=row.extras,
            links=await self.get_links_for_entity(row.id),
        )

    async def get_entities(
        self,
        sphinx_domain: str,
        *,
        limit: int | None = None,
        cursor: IntersphinxEntityCursor | None = None,
    ) -> CountedPaginatedList[IntersphinxEntityLinks, IntersphinxEntityCursor]:
        """Get a page of one Sphinx domain's entities with their links.

        Parameters
        ----------
        sphinx_domain
            The Sphinx domain to list, which scopes the ordering key: a
            name is unique within a domain, not across domains.
        limit
            The maximum number of entities on the page. `None` returns
            every entity in the domain, unpaginated.
        cursor
            A keyset cursor naming the entity the page starts at. `None`
            starts at the first entity.

        Returns
        -------
        CountedPaginatedList
            The page, its neighbouring cursors, and the number of entities
            the domain holds in total.
        """
        stmt = self._entity_select().where(
            SqlIntersphinxEntity.sphinx_domain == sphinx_domain
        )
        return await self._paginate_entities(
            sphinx_domain, stmt, limit=limit, cursor=cursor
        )

    async def get_children(
        self,
        sphinx_domain: str,
        parent_name: str,
        *,
        limit: int | None = None,
        cursor: IntersphinxEntityCursor | None = None,
    ) -> (
        CountedPaginatedList[IntersphinxEntityLinks, IntersphinxEntityCursor]
        | None
    ):
        """Get a page of the entities one entity directly contains.

        Direct children only: a module's page lists its classes, not those
        classes' methods. A caller that wants the whole subtree walks it a
        level at a time, which is what keeps one page one level of the
        hierarchy rather than an unbounded flattening of it.

        The parent is looked up before the children are queried so that a
        name no entity answers to can be told apart from a leaf: the first
        is None here, the second an empty page. Filtering on ``parent_id``
        alone would collapse the two into the same empty answer.

        Parameters
        ----------
        sphinx_domain
            The Sphinx domain both the parent and its children belong to.
        parent_name
            The fully qualified name of the containing entity.
        limit
            The maximum number of children on the page. `None` returns
            every child, unpaginated.
        cursor
            A keyset cursor naming the child the page starts at. `None`
            starts at the first child.

        Returns
        -------
        CountedPaginatedList or None
            The page, its neighbouring cursors, and the number of direct
            children the parent has in total -- or None if the pair names
            no stored entity.
        """
        found = await self._lookup_entity_ids({(sphinx_domain, parent_name)})
        parent_id = found.get((sphinx_domain, parent_name))
        if parent_id is None:
            return None

        stmt = self._entity_select().where(
            # The domain predicate is redundant against ``parent_id`` --
            # a parent is only ever resolved within its own domain -- but
            # it is what the ordering key's uniqueness rests on, so it is
            # stated rather than inferred.
            SqlIntersphinxEntity.sphinx_domain == sphinx_domain,
            SqlIntersphinxEntity.parent_id == parent_id,
        )
        return await self._paginate_entities(
            sphinx_domain, stmt, limit=limit, cursor=cursor
        )

    async def get_links_for_entity(self, entity_id: int) -> list[Link]:
        """Get the documentation links to one entity.

        Parameters
        ----------
        entity_id
            The entity's database ID.

        Returns
        -------
        list of Link
            The links, ordered by the title of the site they point into --
            ``source_collection_title``, not the link's own
            ``source_title``, which is the entity's display name and so is
            the same across sites -- with the URL breaking ties so a reader
            sees the same order on every request.
        """
        stmt = (
            select(SqlIntersphinxLink)
            .where(SqlIntersphinxLink.entity_id == entity_id)
            .order_by(
                SqlIntersphinxLink.source_collection_title,
                SqlIntersphinxLink.html_url,
            )
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            Link(
                html_url=row.html_url,
                type=row.source_type,
                title=row.source_title,
                collection_title=row.source_collection_title,
            )
            for row in rows
        ]

    async def replace_source_links(
        self,
        source_id: int,
        links: Sequence[IntersphinxSourceLink],
        *,
        collection_title: str | None,
    ) -> int:
        """Replace every link one source contributes with the links given.

        Full replace rather than an upsert, because an inventory is a
        complete statement of what a site documents: an object the site has
        dropped is expressed by its absence, and only deleting first can
        read that absence. The delete is scoped to the one source, so a
        re-ingest of one site never disturbs another site's links to the
        same entity.

        Entities are not touched here. A replace that leaves an entity with
        no links from anyone neither deletes it nor unnests what it
        contained: both are decided by `recompute_containment` and
        `prune_orphan_entities`, which the caller runs afterwards and which
        need every source's links rather than this one's.

        Parameters
        ----------
        source_id
            The database ID of the source contributing the links.
        links
            The links the source now contributes. An empty sequence deletes
            the source's links and writes none.
        collection_title
            The title of the documentation site, which every one of its
            links carries as its collection title.

        Returns
        -------
        int
            The number of links written.
        """
        link_table = cast("Table", SqlLink.__table__)
        subtype_table = cast("Table", SqlIntersphinxLink.__table__)

        # Delete the base rows, not the subtype rows: the subtype's own
        # primary key cascades from the base, so one statement clears both,
        # whereas deleting the subtype alone would leave base rows behind
        # with nothing to identify them by.
        await self._session.execute(
            delete(link_table).where(
                link_table.c.id.in_(
                    select(subtype_table.c.id).where(
                        subtype_table.c.source_id == source_id
                    )
                )
            )
        )

        now = datetime.now(tz=UTC).replace(microsecond=0)
        written = 0
        for start in range(0, len(links), _UPSERT_CHUNK_SIZE):
            chunk = links[start : start + _UPSERT_CHUNK_SIZE]
            # ``sort_by_parameter_order`` is what makes the returned IDs
            # line up with the rows that produced them, so the subtype
            # insert below can pair each ID with its entity.
            result = await self._session.execute(
                insert(link_table).returning(
                    link_table.c.id, sort_by_parameter_order=True
                ),
                [
                    {
                        "type": _LINK_DISCRIMINATOR,
                        "html_url": link.html_url,
                        "source_type": link.type,
                        "source_title": link.title,
                        "source_collection_title": collection_title,
                        "date_updated": now,
                    }
                    for link in chunk
                ],
            )
            await self._session.execute(
                insert(subtype_table),
                [
                    {
                        "id": link_id,
                        "entity_id": link.entity_id,
                        "source_id": source_id,
                    }
                    for link_id, link in zip(
                        (row.id for row in result), chunk, strict=True
                    )
                ],
            )
            written += len(chunk)

        await self._session.flush()
        return written

    async def recompute_containment(self) -> int:
        """Derive every entity's parent from the links stored right now.

        Containment is a *derived* fact, not a record of what some past
        ingest saw: an entity's parent is the entity its Sphinx domain's
        hierarchy names as its immediate parent -- the dotted prefix, for
        ``py`` -- and only while at least one source, any source,
        documents that parent. Everything else is top level.

        That rule is what makes stored state independent of ingest order.
        Written parent by parent as each inventory arrived, containment
        would depend on which site was ingested first and would never
        withdraw: a module whose only site stopped publishing its page
        would keep its classes nested under a name nothing documents. Here
        the whole relation is recomputed from the links that exist, so
        ingesting site A then site B leaves exactly what ingesting B alone
        would leave, and a module that loses its last link unnests its
        classes on the spot.

        Deriving containment from links also crosses sites, which is the
        point of storing one entity per name: a class documented by one
        site nests under a module documented by another.

        Run this after every change to the links -- a per-source replace,
        or a source's deletion -- and run `prune_orphan_entities` after it,
        which the ordering matters for: the entities the prune removes are
        exactly the ones with no link, and this statement has already
        refused to point anybody at them.

        Only the Sphinx domains in `SPHINX_DOMAIN_PARENT_NAME_SQL` are
        touched. An entity kind stored here whose containment comes from
        somewhere other than its own name is left entirely alone.

        Returns
        -------
        int
            The number of entities whose parent changed. Rows already
            holding the derived value are not rewritten, so a recompute
            after a sweep that changed nothing costs no writes at all.
        """
        entity_table = cast("Table", SqlIntersphinxEntity.__table__)
        subtype_table = cast("Table", SqlIntersphinxLink.__table__)
        parent = entity_table.alias("parent_entity")

        # One CASE over the domains rather than one statement each, so the
        # recompute stays a single pass however many domains have a rule.
        parent_name = case(
            *[
                (
                    entity_table.c.sphinx_domain == sphinx_domain,
                    parent_name_sql(entity_table.c.name),
                )
                for sphinx_domain, parent_name_sql in (
                    SPHINX_DOMAIN_PARENT_NAME_SQL.items()
                )
            ],
            else_=null(),
        )
        # Null when the name has no parent, when no entity answers to that
        # parent's name, or when nothing documents the one that does --
        # each of which is a top-level entity, and none of which this
        # statement needs to tell apart.
        parent_id = (
            select(parent.c.id)
            .where(
                parent.c.sphinx_domain == entity_table.c.sphinx_domain,
                parent.c.name == parent_name,
                exists().where(subtype_table.c.entity_id == parent.c.id),
            )
            .scalar_subquery()
        )

        result = await self._session.execute(
            update(entity_table)
            .where(
                entity_table.c.sphinx_domain.in_(
                    list(SPHINX_DOMAIN_PARENT_NAME_SQL)
                ),
                # Rewriting every row on every ingest would churn a table
                # whose containment almost never moves; ``IS DISTINCT
                # FROM`` restricts the write to the rows that actually
                # change, nulls included.
                entity_table.c.parent_id.is_distinct_from(parent_id),
            )
            .values(parent_id=parent_id)
        )
        await self._session.flush()
        changed = cast("CursorResult", result).rowcount
        if changed:
            self._logger.info(
                "Recomputed intersphinx entity containment",
                changed_count=changed,
            )
        return changed

    async def prune_orphan_entities(self) -> int:
        """Delete every entity no source links to.

        A link is the only reason to keep an entity. An entity with none is
        not a name held in place by the documented objects beneath it --
        `recompute_containment`, run first, has already turned those
        objects into top-level ones, so nothing is left pointing at the
        rows this deletes.

        Only the Sphinx domains in `SPHINX_DOMAIN_PARENT_NAME_SQL` are
        considered, for the same reason the recompute is so scoped: an
        entity kind whose links live in another subtype table would look
        undocumented here and is not this store's to delete.

        Returns
        -------
        int
            The number of entities deleted.
        """
        entity_table = cast("Table", SqlIntersphinxEntity.__table__)
        subtype_table = cast("Table", SqlIntersphinxLink.__table__)

        result = await self._session.execute(
            delete(entity_table).where(
                entity_table.c.sphinx_domain.in_(
                    list(SPHINX_DOMAIN_PARENT_NAME_SQL)
                ),
                ~exists().where(
                    subtype_table.c.entity_id == entity_table.c.id
                ),
            )
        )
        await self._session.flush()
        pruned = cast("CursorResult", result).rowcount
        if pruned:
            self._logger.info(
                "Pruned intersphinx entities no source documents",
                pruned_count=pruned,
            )
        return pruned

    @staticmethod
    def _entity_select() -> Select:
        """Select the columns an `IntersphinxEntityLinks` is built from.

        ``id`` rides along unused by the model itself: the read path needs
        it to fetch the entity's links, and Pydantic ignores the extra
        attribute when it validates the row.
        """
        parent = aliased(SqlIntersphinxEntity)
        return (
            select(
                SqlIntersphinxEntity.id,
                SqlIntersphinxEntity.sphinx_domain,
                SqlIntersphinxEntity.name,
                SqlIntersphinxEntity.role,
                SqlIntersphinxEntity.display_name,
                SqlIntersphinxEntity.extras,
                parent.name.label("parent_name"),
            )
            .select_from(SqlIntersphinxEntity)
            .outerjoin(parent, parent.id == SqlIntersphinxEntity.parent_id)
        )

    async def _paginate_entities(
        self,
        sphinx_domain: str,
        stmt: Select,
        *,
        limit: int | None,
        cursor: IntersphinxEntityCursor | None,
    ) -> CountedPaginatedList[IntersphinxEntityLinks, IntersphinxEntityCursor]:
        """Page an entity select and fill each entry's links in.

        The links are fetched in a second query rather than joined onto the
        first: a join would return one row per link and so pay out a page
        of entities as a page of links, breaking the very thing keyset
        pagination is for. Two queries keep one page one page, however many
        sites document the entities on it.

        Parameters
        ----------
        sphinx_domain
            The Sphinx domain *stmt* is scoped to, which is what makes the
            entity name a usable key for the links query.
        stmt
            A select of the columns `_entity_select` produces, narrowed to
            the entities the page is drawn from.
        limit
            The maximum number of entities on the page, or `None` for all
            of them.
        cursor
            A keyset cursor naming the entity the page starts at, or
            `None` to start at the first.
        """
        runner = CountedPaginatedQueryRunner(
            entry_type=IntersphinxEntityLinks,
            cursor_type=IntersphinxEntityCursor,
        )
        page = await runner.query_row(
            session=self._session, stmt=stmt, cursor=cursor, limit=limit
        )
        links = await self._get_links_by_entity_name(
            sphinx_domain, [entry.name for entry in page.entries]
        )
        return CountedPaginatedList[
            IntersphinxEntityLinks, IntersphinxEntityCursor
        ](
            entries=[
                entry.model_copy(update={"links": links.get(entry.name, [])})
                for entry in page.entries
            ],
            next_cursor=page.next_cursor,
            prev_cursor=page.prev_cursor,
            count=page.count,
        )

    async def _get_links_by_entity_name(
        self, sphinx_domain: str, names: Sequence[str]
    ) -> dict[str, list[Link]]:
        """Get the links to each named entity, keyed by entity name.

        Keyed by name rather than by database ID because the caller holds
        `IntersphinxEntityLinks` models, which carry no ID -- and a name is
        just as good a key here, being unique within a Sphinx domain.

        Each entity's links are ordered by the title of the site they point
        into, with the URL breaking ties, matching
        `get_links_for_entity`.

        Entities nothing links to are simply absent from the mapping.
        """
        if not names:
            return {}

        stmt = (
            select(
                SqlIntersphinxEntity.name,
                SqlIntersphinxLink.html_url,
                SqlIntersphinxLink.source_type,
                SqlIntersphinxLink.source_title,
                SqlIntersphinxLink.source_collection_title,
            )
            .select_from(SqlIntersphinxLink)
            .join(
                SqlIntersphinxEntity,
                SqlIntersphinxEntity.id == SqlIntersphinxLink.entity_id,
            )
            .where(
                SqlIntersphinxEntity.sphinx_domain == sphinx_domain,
                SqlIntersphinxEntity.name.in_(names),
            )
            # The same order `get_links_for_entity` reads one entity's
            # links in -- by the title of the site each link points into,
            # then by URL -- so a link list does not depend on which
            # endpoint served it.
            .order_by(
                SqlIntersphinxLink.source_collection_title,
                SqlIntersphinxLink.html_url,
            )
        )
        links: dict[str, list[Link]] = {}
        for row in await self._session.execute(stmt):
            links.setdefault(row.name, []).append(
                Link(
                    html_url=row.html_url,
                    type=row.source_type,
                    title=row.source_title,
                    collection_title=row.source_collection_title,
                )
            )
        return links

    @staticmethod
    def _deduplicate(
        entities: Iterable[InventoryEntity],
    ) -> list[InventoryEntity]:
        """Return the entities with each identity kept once, first wins."""
        merged: dict[tuple[str, str], InventoryEntity] = {}
        for entity in entities:
            merged.setdefault((entity.sphinx_domain, entity.name), entity)
        return list(merged.values())

    async def _upsert_rows(
        self, entities: Sequence[InventoryEntity]
    ) -> dict[tuple[str, str], int]:
        """Upsert the entity rows and return their IDs by identity.

        Neither ``parent_id`` nor ``extras`` appears here: the first is
        derived from the links by `recompute_containment` and the second
        has no source in an inventory, and naming either would overwrite a
        stored value with a null on every re-ingest.
        """
        entity_ids: dict[tuple[str, str], int] = {}
        for start in range(0, len(entities), _UPSERT_CHUNK_SIZE):
            chunk = entities[start : start + _UPSERT_CHUNK_SIZE]
            statement = pg_insert(SqlIntersphinxEntity).values(
                [
                    {
                        "sphinx_domain": entity.sphinx_domain,
                        "name": entity.name,
                        "role": entity.role,
                        "display_name": entity.display_name,
                    }
                    for entity in chunk
                ]
            )
            result = await self._session.execute(
                statement.on_conflict_do_update(
                    constraint="uq_intersphinx_entity_name",
                    set_={
                        "role": statement.excluded.role,
                        "display_name": statement.excluded.display_name,
                    },
                ).returning(
                    SqlIntersphinxEntity.id,
                    SqlIntersphinxEntity.sphinx_domain,
                    SqlIntersphinxEntity.name,
                )
            )
            for row in result:
                entity_ids[row.sphinx_domain, row.name] = row.id
        return entity_ids

    async def _lookup_entity_ids(
        self, identities: set[tuple[str, str]]
    ) -> dict[tuple[str, str], int]:
        """Return the IDs of the identities that name a stored entity.

        Identities with no stored entity are simply absent, which is what
        leaves an entity whose parent nobody documents at the top level.
        """
        found: dict[tuple[str, str], int] = {}
        ordered = sorted(identities)
        for start in range(0, len(ordered), _UPSERT_CHUNK_SIZE):
            chunk = ordered[start : start + _UPSERT_CHUNK_SIZE]
            stmt = select(
                SqlIntersphinxEntity.id,
                SqlIntersphinxEntity.sphinx_domain,
                SqlIntersphinxEntity.name,
            ).where(
                # A tuple IN comparison so one round trip covers identities
                # spread across Sphinx domains.
                tuple_(
                    SqlIntersphinxEntity.sphinx_domain,
                    SqlIntersphinxEntity.name,
                ).in_(chunk)
            )
            for row in await self._session.execute(stmt):
                found[row.sphinx_domain, row.name] = row.id
        return found


@dataclass(slots=True)
class IntersphinxEntityCursor(PaginationCursor[IntersphinxEntityLinks]):
    """A keyset pagination cursor over entities in one Sphinx domain.

    Ordered by name alone, which is a complete key rather than a prefix of
    one: ``uq_intersphinx_entity_name`` makes a name unique within its
    Sphinx domain, and a collection query is always scoped to one domain.
    That is what makes paging stable -- no tiebreak is needed, so no two
    rows can swap places between pages and be dropped or served twice.
    """

    name: str
    """The name of the entity the page starts at."""

    @override
    @classmethod
    def from_entry(
        cls, entry: IntersphinxEntityLinks, *, reverse: bool = False
    ) -> Self:
        """Construct a cursor with an entry as the bound."""
        return cls(name=entry.name, previous=reverse)

    @override
    @classmethod
    def from_str(cls, cursor: str) -> Self:
        """Build a cursor from its string serialization."""
        try:
            decoded = base64.b64decode(cursor).decode("utf-8")
            data = json.loads(decoded)
            return cls(name=data["name"], previous=data["previous"])
        except Exception as e:
            raise InvalidCursorError(f"Cannot parse cursor: {e!s}") from e

    @override
    @classmethod
    def apply_order(cls, stmt: Select, *, reverse: bool = False) -> Select:
        """Apply the cursor's sort order to a select statement."""
        column = SqlIntersphinxEntity.name
        return stmt.order_by(column.desc() if reverse else column.asc())

    @override
    def apply_cursor(self, stmt: Select) -> Select:
        """Apply the cursor's bound to a select statement."""
        column = SqlIntersphinxEntity.name
        # Inclusive going forwards and exclusive going back, because a
        # cursor names the first entry of the page it opens: that entry
        # belongs to the next page and not to the previous one.
        if self.previous:
            return stmt.where(column < self.name)
        return stmt.where(column >= self.name)

    @override
    def invert(self) -> Self:
        return type(self)(name=self.name, previous=not self.previous)

    def __str__(self) -> str:
        """Serialize to a string, the inverse of `from_str`."""
        data = {"name": self.name, "previous": self.previous}
        encoded = base64.b64encode(json.dumps(data).encode("utf-8"))
        return encoded.decode("utf-8")
