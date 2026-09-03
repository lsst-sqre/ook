"""Storage interface for entities parsed out of Sphinx object inventories."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Self, cast, override

from safir.database import (
    CountedPaginatedList,
    CountedPaginatedQueryRunner,
    InvalidCursorError,
    PaginationCursor,
)
from sqlalchemy import (
    CursorResult,
    Select,
    Table,
    bindparam,
    delete,
    insert,
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
    IntersphinxEntityLinks,
    IntersphinxSourceLink,
    InventoryEntity,
)
from ook.domain.links import Link

__all__ = ["IntersphinxEntityCursor", "IntersphinxEntityStore"]


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
        """Insert or update entities, resolving each one's parent by name.

        Entities are written in two passes because a batch routinely names
        parents it also contains: every row is upserted first, so the second
        pass can resolve a parent name against the database whether the
        parent arrived in this batch, in an earlier one, or from another
        source entirely.

        An entity with no parent name leaves the stored ``parent_id``
        alone rather than clearing it. Entities are shared across sources,
        so a site that documents a class without its module must not
        withdraw the containment a site documenting both established.
        Withdrawing a relationship is the pruning path's business.

        `InventoryEntity.uri` is not read here: it locates the object on
        one particular site, which makes it a property of that site's link
        rather than of the entity every site shares.

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
        await self._resolve_parents(deduplicated, entity_ids)
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
        no links from anyone does not delete it -- that is
        `prune_orphan_entities`' decision, and it needs the whole picture
        rather than one source's.

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

    async def prune_orphan_entities(self) -> int:
        """Delete entities no source documents, directly or below them.

        An entity is kept if any source links to it, or if any entity below
        it in the hierarchy is kept. The second clause is what keeps a
        package whose own page nobody publishes -- common, since a site may
        document a module's classes without giving the module a page of its
        own -- from being deleted out from under the classes it contains.

        Deleting a subtree is safe in either order: an entity kept for a
        descendant's sake pulls its whole ancestry into the kept set, so a
        deleted entity never has a kept child left pointing at it.

        Returns
        -------
        int
            The number of entities deleted.
        """
        entity_table = cast("Table", SqlIntersphinxEntity.__table__)
        subtype_table = cast("Table", SqlIntersphinxLink.__table__)

        # Walk *up* from every linked entity rather than down from the
        # roots: the question is which entities have a documented
        # descendant, and the ancestors of the linked set are exactly the
        # answer, in one pass over the linked rows instead of one per root.
        kept = (
            select(entity_table.c.id, entity_table.c.parent_id)
            .join(
                subtype_table, subtype_table.c.entity_id == entity_table.c.id
            )
            .distinct()
            .cte("kept_intersphinx_entity", recursive=True)
        )
        ancestor = entity_table.alias("ancestor")
        kept = kept.union(
            select(ancestor.c.id, ancestor.c.parent_id).join(
                kept, kept.c.parent_id == ancestor.c.id
            )
        )

        result = await self._session.execute(
            delete(entity_table).where(
                entity_table.c.id.not_in(select(kept.c.id))
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
        written by the second pass and the second has no source in an
        inventory, and naming either would overwrite a stored value with a
        null on every re-ingest.
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

    async def _resolve_parents(
        self,
        entities: Sequence[InventoryEntity],
        entity_ids: dict[tuple[str, str], int],
    ) -> None:
        """Point each entity that names a stored parent at that parent."""
        wanted = {
            (entity.sphinx_domain, entity.parent_name)
            for entity in entities
            if entity.parent_name is not None
        }
        if not wanted:
            return

        parent_ids = await self._lookup_entity_ids(wanted)
        updates = [
            {
                "b_entity_id": entity_ids[entity.sphinx_domain, entity.name],
                "b_parent_id": parent_ids[
                    entity.sphinx_domain, entity.parent_name
                ],
            }
            for entity in entities
            if entity.parent_name is not None
            and (entity.sphinx_domain, entity.parent_name) in parent_ids
        ]
        if not updates:
            return

        # Against the Core table rather than the mapped class: the ORM's
        # executemany UPDATE is a per-row update *by primary key*, which
        # cannot carry a WHERE clause of its own, and there is nothing for
        # it to synchronize here anyway -- these rows were written by the
        # INSERT above and are read back by a fresh SELECT.
        table = cast("Table", SqlIntersphinxEntity.__table__)
        await self._session.execute(
            update(table)
            .where(table.c.id == bindparam("b_entity_id"))
            .values(parent_id=bindparam("b_parent_id")),
            updates,
        )

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
