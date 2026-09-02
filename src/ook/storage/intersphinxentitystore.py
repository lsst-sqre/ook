"""Storage interface for entities parsed out of Sphinx object inventories."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import (
    CursorResult,
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

__all__ = ["IntersphinxEntityStore"]


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
        parent = aliased(SqlIntersphinxEntity)
        stmt = (
            select(
                SqlIntersphinxEntity.id,
                SqlIntersphinxEntity.sphinx_domain,
                SqlIntersphinxEntity.name,
                SqlIntersphinxEntity.role,
                SqlIntersphinxEntity.dispname,
                SqlIntersphinxEntity.extras,
                parent.name.label("parent_name"),
            )
            .select_from(SqlIntersphinxEntity)
            .outerjoin(parent, parent.id == SqlIntersphinxEntity.parent_id)
            .where(
                SqlIntersphinxEntity.sphinx_domain == sphinx_domain,
                SqlIntersphinxEntity.name == name,
            )
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None

        return IntersphinxEntityLinks(
            sphinx_domain=row.sphinx_domain,
            name=row.name,
            role=row.role,
            dispname=row.dispname,
            parent_name=row.parent_name,
            extras=row.extras,
            links=await self.get_links_for_entity(row.id),
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
            The links, ordered by the title of the site they point into so
            a reader sees the same order on every request.
        """
        stmt = (
            select(SqlIntersphinxLink)
            .where(SqlIntersphinxLink.entity_id == entity_id)
            .order_by(
                SqlIntersphinxLink.source_title, SqlIntersphinxLink.html_url
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
                        "dispname": entity.dispname,
                    }
                    for entity in chunk
                ]
            )
            result = await self._session.execute(
                statement.on_conflict_do_update(
                    constraint="uq_intersphinx_entity_name",
                    set_={
                        "role": statement.excluded.role,
                        "dispname": statement.excluded.dispname,
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
