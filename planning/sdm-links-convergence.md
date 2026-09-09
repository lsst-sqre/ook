# SDM links convergence design note

How the SDM domain's per-entity link tables could move onto the generic entity model that DM-55388 built for the Python link domain, what that model is missing before they can, and which of those gaps are cheap and which are decisions.

> [!IMPORTANT]
> **Nothing in this note lands under DM-55388.** The generic entity model, its ingest pipeline, and the `python` link domain are what that ticket delivers; migrating the SDM domain onto that model is explicitly out of scope for it (PRD [ook#237](https://github.com/lsst-sqre/ook/issues/237), "Out of scope": *"Migrating the SDM domain onto the generic entity table (follow-up PRD; only the design note lands here)"*). This document exists to seed that follow-up PRD. It proposes no migration, changes no code, and deliberately leaves several questions open for the interview that writes the PRD.

Written 2026-09-02 against the schema as built by migration `ec0c04ddd611` (task [ook#265](https://github.com/lsst-sqre/ook/issues/265)) under task [ook#272](https://github.com/lsst-sqre/ook/issues/272).

> **Column naming.** The entity's display-name column is named `display_name` (renamed from the Sphinx wire-format spelling `dispname` that `ec0c04ddd611` created it with, under task [ook#393](https://github.com/lsst-sqre/ook/issues/393)).

## 1. The two models, as built

The Links API today serves two domains from two unrelated storage shapes that meet only at the polymorphic `link` table.

| | SDM domain | Generic (intersphinx) domain |
| --- | --- | --- |
| Entity tables | `sdm_schema`, `sdm_table`, `sdm_column` — one table per level, fixed depth 3 | `intersphinx_entity` — one table, self-referential `parent_id`, unbounded depth |
| Entity identity | `sdm_schema.name`; `(schema_id, name)`; `(table_id, name)` — each scoped to its parent | `(sphinx_domain, name)`, `uq_intersphinx_entity_name` — global within a domain |
| Link subtypes | `links_sdm_schemas`, `links_sdm_tables`, `links_sdm_columns` (`polymorphic_identity` `sdm_schema` / `sdm_table` / `sdm_column`) | `links_intersphinx` (`polymorphic_identity` `intersphinx`) |
| Link → entity | one FK per subtype (`schema_id` / `table_id` / `column_id`) | `entity_id` FK, plus a `source_id` FK naming the site the link came from |
| Where links come from | one hard-coded site: the schema browser at `sdm-schemas.lsst.io`, built inside `SdmSchemasIngestService` | any registered site in `intersphinx_source` |
| Sibling ordering | `tap_table_index`, `tap_column_index` — typed nullable `BIGINT` columns | none; siblings order by `name` |
| Stale-record policy | timestamp-window sweep: upsert everything with `date_updated = now`, then delete rows in the schema whose `date_updated < now - 2s` | per-source replace of `links_intersphinx`, then a recursive-CTE prune of entities nothing links to and nothing below links to |
| Entity attributes with no generic home | `felis_id`, `description`, `datatype`, `ivoa_ucd`, `ivoa_unit`, `tap_*_index`, `github_owner/repo/ref/path` | `extras` JSONB (nothing writes it yet) |

The generic side's columns, as `ec0c04ddd611` created them:

- `intersphinx_entity`: `id`, `sphinx_domain`, `name`, `role`, `display_name`, `parent_id` (self-FK, `ON DELETE SET NULL`, indexed), `extras` (JSONB, nullable). Unique on `(sphinx_domain, name)`.
- `intersphinx_source`: `id`, `url` (unique), `title`, `enabled`, `date_ingested`, `last_status`, `last_error`.
- `links_intersphinx`: `id` (FK `link.id`, `ON DELETE CASCADE`), `entity_id` (FK `intersphinx_entity.id`, `CASCADE`), `source_id` (FK `intersphinx_source.id`, `CASCADE`).
- `link` (shared base): `id`, `type`, `html_url`, `source_type`, `source_title`, `source_collection_title`, `date_updated`.

## 2. The mapping

### 2.1 Identity: the dotted name is already the SDM name

SDM entities have a natural fully qualified name — the TAP/ADQL one a user already writes in a query:

| SDM row | Converged `name` | `role` | `display_name` |
| --- | --- | --- | --- |
| `sdm_schema` "dp02_dc2_catalogs" | `dp02_dc2_catalogs` | `schema` | `dp02_dc2_catalogs` |
| `sdm_table` "Object" in it | `dp02_dc2_catalogs.Object` | `table` | `Object` |
| `sdm_column` "coord_ra" in that | `dp02_dc2_catalogs.Object.coord_ra` | `column` | `coord_ra` |

Three things fall out for free:

- **`role` is already the SDM entity type.** `schema` / `table` / `column` are exactly the values `SdmSchemaLinkedEntityInfo.domain_type` and friends publish, so the API's `domain_type` reads straight off `intersphinx_entity.role` with no mapping table.
- **`display_name` earns its keep.** Sphinx's `dispname`-vs-`name` distinction (short label vs. qualified target) is the same distinction SDM needs: `name` is `dp02_dc2_catalogs.Object`, `display_name` is `Object`. That is what today's SDM link titles are built from (`f"{table.name} table"`), so the converged link title is `f"{display_name} {role}"` — see §2.4.
- **`PythonHierarchy` works unchanged.** Splitting at the last dot recovers `dp02_dc2_catalogs.Object` from `dp02_dc2_catalogs.Object.coord_ra`. An `SdmHierarchy` strategy satisfying the `SphinxDomainHierarchy` protocol would be `PythonHierarchy` verbatim; whether to register the same object under a second key or introduce a distinct class is a style question, not a design one.

**The identity equivalence has a precondition:** the dotted join is lossless only if no schema, table, or column name contains a dot. SDM's per-parent uniqueness (`uq_sdm_table_schema_name`, `uq_sdm_column_table_name`) and the generic model's per-domain uniqueness agree exactly under that condition, and diverge if it fails — `a.b` + `c` and `a` + `b.c` would collide on one row. The follow-up should assert dot-free names at ingest and fail loudly, rather than silently merging two columns. Felis identifiers are SQL identifiers, so this is an assertion, not an expected case.

### 2.2 Hierarchy: same tree, stricter parents

The generic model's `parent_id` reproduces `sdm_table.schema_id` and `sdm_column.table_id` with one behavioral difference worth naming: `build_entities` links a parent **only when the same ingest documents it**, and leaves the child top level otherwise. For Sphinx that is essential (a site may document a class without its module). For SDM it should never happen — Felis always defines a column inside a table inside a schema — so an SDM ingest that produces a top-level `table` row is a bug, and the follow-up should treat a missing parent in the `sdm` domain as an error rather than as the tolerated gap it is in `py`.

`ON DELETE SET NULL` on `parent_id` is likewise a Sphinx accommodation. For SDM, deleting a table should delete its columns, not orphan them into the top level. Either the SDM ingest deletes bottom-up explicitly, or the follow-up revisits the FK action; the latter would change `py` semantics, so the former is likely.

### 2.3 The link row

`links_intersphinx` needs nothing new. An SDM link is one `(entity, source)` pair with an absolute `html_url`, which is what the table already holds. The three SDM subtypes collapse into it and `links_sdm_schemas` / `links_sdm_tables` / `links_sdm_columns` are dropped along with their `polymorphic_identity` values.

### 2.4 Link titles and types

Today's intersphinx ingest sets `title=entity.display_name` and `type=SPHINX_DOMAIN_LINK_TYPES[domain]` in `IntersphinxIngestService._build_links`. SDM's convention is `title=f"{short_name} {role}"` (`"Object table"`) and `type="schema_browser"`.

- **`type`** is domain-keyed today and stays so: `SPHINX_DOMAIN_LINK_TYPES` gains `{"sdm": "schema_browser"}` alongside `{"py": "python_api"}`. It is not a per-source property — every site that documents an SDM column is documenting the same *kind* of thing.
- **`title`** is not domain-keyed today; it is hard-coded to the display name. Convergence needs a per-domain title strategy alongside `SphinxDomainHierarchy` — a one-method protocol returning a title from `(display_name, role)`, whose `py` implementation returns `display_name` and whose `sdm` implementation returns `f"{display_name} {role}"`. Small, but it is the one place `_build_links` stops being domain-neutral.
- **`collection_title`** already matches: the SDM ingest hard-codes `"Science Data Model Schemas"`, which is precisely what `intersphinx_source.title` is for.

### 2.5 The source registry does not fit as-is

`intersphinx_source`'s identity is `url`, "the full URL of the site's `objects.inv` inventory", and the ingest path fetches that URL through the DM-55387 inventory cache. SDM has no inventory: its entities come from Felis YAML in a GitHub repo, and its links from schema-browser Markdown in the same repo.

So convergence needs the registry to describe *a place links come from* rather than *an inventory URL*. Options, in increasing cost:

1. **Register the schema browser's own `objects.inv`** if `sdm-schemas.lsst.io` publishes one, and let the existing pipeline ingest it as an ordinary Sphinx site. This is by far the cheapest path and would make SDM convergence largely a matter of deleting code — but it only works if the browser's inventory anchors match the Felis-ID anchors the current links use, and if the inventory's `std` labels can be mapped onto schema/table/column roles. **This should be checked first**, because a positive answer changes the shape of the whole follow-up PRD.
2. **Add a `kind` discriminator** to `intersphinx_source` (`inventory` | `sdm_felis`, say) and make `url` its generic identity — the repo URL for SDM. The registry keeps one row per source, the per-source replace and the `last_status` / `last_error` observability apply unchanged, and only the fetch-and-parse step branches on `kind`.
3. **Leave SDM's ingest outside the registry** and let it write `links_intersphinx` rows against a synthetic source row. This works but throws away the failure-stamping and enable/disable behavior that make the registry worth having.

Option 2 is the recommended default if option 1 does not pan out.

## 3. Typed TAP sort keys and the `extras` escape hatch

PRD ook#237 names "typed TAP sort keys → `extras` JSONB" as a thing this note must cover. **The recommendation is not to put them there.**

`extras` is documented as "domain-specific attributes that have no column of their own" — an escape hatch for *payload*: values Ook stores, hands back, and never reasons about. A sort key is not payload. It is an ordering input consumed by `ORDER BY`, by a keyset cursor's `WHERE`, and by the index that has to serve both. Every one of those has a problem with JSONB:

- **`->>` yields `text`, and text ordering is wrong.** `'10' < '9'`, so ordering by `extras->>'tap_column_index'` silently mis-sorts any table with ten or more columns. A cast is mandatory.
- **The cast turns a data error into an outage.** `(extras->>'tap_column_index')::bigint` raises `invalid input syntax for type bigint` when *any* scanned row holds a non-numeric value. One bad ingest — a value written as `"3"` where `3` was meant is enough to be plausible — takes out the whole collection endpoint, and blocks creating the expression index too. A typed `BIGINT` column rejects the same mistake at write time, in the ingest that caused it.
- **Ordering the raw `jsonb` value works, and shouldn't be relied on.** `extras->'tap_column_index'` does order numerically, because jsonb comparison orders within the Number type by value, and a missing key yields SQL `NULL` so `NULLS LAST` still applies. But it silently reorders if a value is ever stored as a string (jsonb sorts `String > Number`), and the correctness of the endpoint then rests on a fact about jsonb's type ordering that no reader of the query will know.
- **Indexing costs an expression index.** `CREATE INDEX ... ON intersphinx_entity (((extras->>'tap_column_index')::bigint))` is legal (`text::bigint` is immutable), but it has to be written identically to the query, it cannot be a plain composite with `parent_id` and `name` without more expression columns, and it is invalidated by exactly the bad-row case above.
- **The cursor loses its type.** `IntersphinxEntityCursor` serializes typed Python values to JSON. A JSONB-sourced sort key arrives as "whatever was in the document", so `apply_cursor`'s comparison has to re-derive the type it is comparing at, and a cursor minted before a type correction compares differently after it.

### 3.1 Recommendation: one typed nullable sibling sort key

Add a first-class column to `intersphinx_entity`:

```
sibling_sort_key BIGINT NULL
```

meaning: **where this entity sorts among the other children of its parent.** `NULL` means "no explicit order given; fall back to `name`."

The unification that makes this a single column rather than two is that SDM's two indexes never apply to the same row:

- `tap_table_index` orders tables — the children of a schema.
- `tap_column_index` orders columns — the children of a table.

A row is only ever ordered among its own siblings, so each SDM row has exactly one relevant index, and the converged column holds it. The `py` domain leaves it `NULL` throughout and its ordering is unchanged. The supporting index is a plain composite, `(parent_id, sibling_sort_key, name)`, with no expressions — and note that Postgres `ASC` already defaults to `NULLS LAST`, which is exactly the "treat `NULL` as infinitely large" convention `SdmTableLinksCollectionCursor` and `SdmColumnLinksCollectionCursor` document today. The index order and the desired order coincide with no `NULLS LAST` decoration at all.

`tap_table_index` and `tap_column_index` can still be echoed into `extras` if a consumer ever wants them under their SDM names, but nothing in the Links API reads them today (see §6), so the follow-up should probably not bother.

This is a proposal, not a settled decision — the alternative of keeping the sort key in `extras` and paying for an expression index is defensible if the follow-up decides `intersphinx_entity` must stay strictly generic. The tradeoff is written out above so the PRD interview can make that call deliberately.

## 4. Pagination cursors

Two orderings exist in the Links API, they are not the same problem, and conflating them is the main way convergence could go wrong.

### 4.1 Sibling order — `/children` and the SDM scoped collections

`GET /links/domains/python/objects/{name}/children` pages the children of one entity. `GET /links/domains/sdm/schemas/{schema}/tables` and `.../tables/{table}/columns` do the same thing under different URLs. This is the ordering `sibling_sort_key` serves, and the converged cursor is **`SdmColumnLinksCollectionCursor` generalized**: rename `tap_column_index` → `sibling_sort_key` and `column_name` → `name`, and its `apply_order` / `apply_cursor` / `invert` / `__str__` transfer unchanged, NULL-handling branches included.

Those NULL branches are irreducible and should be carried over rather than reinvented. `ORDER BY` gets `NULLS LAST` for free, but the cursor's `WHERE` cannot: SQL comparison against `NULL` is unknown, so "rows at or after a `NULL` position" has to be spelled out, which is what the existing three-branch `apply_cursor` does. Convergence gets to delete two of the three hand-rolled SDM cursors, not zero of them — the third becomes the generic one.

The key stays complete without a tiebreak, for the reason `IntersphinxEntityCursor` documents: `uq_intersphinx_entity_name` makes `name` unique within a domain, so `(sibling_sort_key, name)` cannot tie.

### 4.2 Document order — the flat collections

`GET /links/domains/python/objects` orders by `name` alone. `GET /links/domains/sdm/schemas` orders by `(schema_name, table_name, column_name)` with `''` padding the levels an entity does not have, so a schema sorts immediately before its tables and a table immediately before its columns.

**The flat SDM collection already discards TAP order.** `SdmLinksCollectionCursor` sorts by names only; only the scoped collections use the TAP indexes. So the flat listing has never promised TAP order, and convergence does not owe it one. This is the single most useful fact in this section: it means no materialized path, no ordered ancestor array, and no `ltree` is needed. A generic tree cannot express a global document order in a `(sort_key, name)` tuple — sibling keys collide across parents — and if the flat collection *did* have to preserve TAP order, the follow-up would be committed to maintaining a path column and rebuilding whole subtrees whenever a parent's key changed. It does not.

What it needs instead is for `ORDER BY name` on dotted names to reproduce the `('', '')`-padded tuple order. That holds — under the right collation.

### 4.3 The collation trap

`SdmLinksCollectionCursor`'s padded-tuple order and a plain `ORDER BY name` over dotted names agree only if `.` sorts below every character that can appear in a name. Probed against `postgres:16`:

```
-- default collation (en_US.utf8, the image and cluster default; ook sets none)
s | s.T | s.T-a | s.T_a | s.Ta | s.T.z

-- COLLATE "C"
s | s.T | s.T-a | s.T.z | s.T_a | s.Ta
```

Under the default `en_US.utf8` collation the equivalence **fails**: glibc ignores punctuation at the primary level, so `s.T.z` — a column of table `T` — sorts *after* the sibling table `s.Ta`, interleaving one table's columns with another table. Under `COLLATE "C"` it holds, because `.` (0x2E) sorts below the alphanumerics and `_` (0x5F) that make up SQL identifiers.

Consequences for the follow-up:

- The flat collection's ordering, its cursor comparison, and its supporting index must all be pinned to `COLLATE "C"` — or `intersphinx_entity.name` declared as `UnicodeText COLLATE "C"`, which pins all three at once and is the less error-prone option. An index built under one collation cannot serve an `ORDER BY` under another, so a mismatch is a silent sequential-scan-and-sort as well as a correctness bug.
- `C` is still not a total guarantee: a name containing a character below `.` — `-` or a space — breaks document order even there, as `s.T-a` above shows. SQL identifiers exclude these, so it is another ingest-time assertion rather than an expected case, and it is the same assertion §2.1 already wants for dots.
- **The `py` domain today is unaffected.** Its ordering and its cursor comparison use the same default collation, so paging is self-consistent and no row is dropped or served twice. Only the *claim in this section* — that dotted names reproduce SDM's tuple order — needs `C`. Changing the collation is therefore a convergence decision, not a bug fix to backport.

### 4.4 Cursor wire format

A converged `/children` cursor serializes `{sibling_sort_key, name, previous}`; today's SDM cursors serialize `{tap_table_index, table_name, previous}` and `{tap_column_index, column_name, previous}`. `from_str` raises `InvalidCursorError` on an unrecognized payload, which the API surfaces as a 422. Cursors are opaque and short-lived, so the honest plan is to accept that in-flight cursors break across the cutover and say so in the changelog, rather than to build a compatibility shim for a value nobody bookmarks. The follow-up should confirm that assumption rather than inherit it from this note.

## 5. Felis IDs

A Felis `@id` does three jobs today, and they land in three different places.

**1. It is the schema-browser anchor, and it is already baked into the link.** `SdmSchemasIngestService` builds `html_url=f"{schema_url}{table.felis_id}"` — the Felis ID carries its own leading `#`. That absolute URL is exactly what `links_intersphinx.html_url` holds and exactly what the intersphinx ingest already computes (`urljoin(source.url, entity.uri)`). **Nothing has to move for links to keep working.** The anchor is a property of "this site documents this object at this URL", which is the link, not the entity — and the current SDM design agrees, since `felis_id` lives on the entity only because the link is assembled from it at ingest time.

One wart to clean up if the ID is stored anywhere: the value includes its `#`, so `extras["felis_id"] = "#Object"` rather than `"Object"`. Normalize on the way in.

**2. It is Felis's internal cross-reference target** — what `primaryKey` and index definitions in the YAML point at. Ook does not read it for that purpose and has no plans to. If a future consumer wants it, `extras` is the correct home: opaque payload, stored and handed back, never reasoned about. This is the textbook `extras` case, and the contrast with §3's sort keys is the point — payload belongs there, ordering inputs do not.

**3. It is stable across renames, and Ook's identity is not.** A Felis object keeps its `@id` when its `name` changes; `(sphinx_domain, name)` does not. So a rename forks the entity: the new name is a new row and the old row is pruned, losing nothing but also carrying nothing forward. **This is not a regression** — `SdmSchemasStore.update_schema` upserts on `(schema_id, name)` and sweeps the old row today, so a rename already forks. Convergence preserves current behavior.

If rename-stable identity is ever wanted, note that it *is* expressible: `CREATE UNIQUE INDEX ... ON intersphinx_entity ((extras->>'felis_id')) WHERE sphinx_domain = 'sdm'` is a legal partial expression index. But that installs a second identity competing with `(sphinx_domain, name)`, and every write path would have to decide which one wins. It should be a deliberate feature with its own requirements, never a side effect of "we had `extras` lying around."

## 6. What `extras` should actually hold

Working from what the Links API publishes rather than what the SDM tables store. `SdmSchemaLinkedEntityInfo`, `SdmTableLinkedEntityInfo`, and `SdmColumnLinkedEntityInfo` carry only `domain`, `domain_type`, the name path, and `self_url`. **The Links API exposes none of `description`, `datatype`, `ivoa_ucd`, `ivoa_unit`, `tap_table_index`, `tap_column_index`, `felis_id`, or the `github_*` columns.**

So the honest inventory is:

| SDM column | Home after convergence |
| --- | --- |
| `sdm_*.name` | `intersphinx_entity.name` (qualified) + `display_name` (short) |
| entity type | `intersphinx_entity.role` |
| `tap_table_index`, `tap_column_index` | `intersphinx_entity.sibling_sort_key` (§3.1) |
| `felis_id` (as anchor) | already inside `links_intersphinx.html_url` (§5) |
| `felis_id` (as Felis identity) | `extras`, if a consumer appears |
| `description` | `extras`, or dropped |
| `datatype`, `ivoa_ucd`, `ivoa_unit` | `extras`, or dropped |
| `github_owner`, `github_repo`, `github_ref`, `github_path` | `intersphinx_source`, not the entity — they describe where the source came from, and they are per-schema only because SDM's registry is implicit |
| `date_updated` | `link.date_updated` (already on the base table) |

`SdmSchemasStore.get_schema`, `list_schemas`, and `get_schema_by_repo_path` have no callers outside the store and `SdmSchemasIngestService` — nothing else in Ook reads the `sdm_*` tables — so "or dropped" is genuinely on the table. **But dropping is a one-way door**, and a converged `sdm` domain with no `description` cannot grow a `GET /links/domains/sdm/objects/{name}` that returns one without re-ingesting the world. Preserving them in `extras` costs a JSONB write per row and keeps the option; that is the recommendation, with the PRD free to disagree.

## 7. The mismatch that needs a decision: what makes an entity exist

This is the sharpest semantic difference between the two models, and it is not a naming or indexing question.

In the generic model, **an entity exists because a source links to it.** `prune_orphan_entities` deletes every entity that no `links_intersphinx` row points at and that has no kept descendant. In the SDM model, **an entity exists because Felis defines it.** `sdm_column` rows are written from the YAML; whether the schema browser happens to anchor them is a separate ingest step.

Converged naively, an SDM column the schema browser does not anchor would be pruned out of existence — where today it sits in `sdm_column` with an empty `links` list. Whether that matters depends on whether the schema browser anchors *every* column (it appears to: the ingest emits a `SdmColumnLink` for every column of every table), but designing on that coincidence is how a future browser-template change silently deletes half the SDM domain.

Three ways out, for the PRD to choose between:

1. **Accept it.** If every Felis object is always anchored, links and definitions coincide and the prune is harmless. Cheapest, and fragile in exactly the way described.
2. **Let a source assert existence without linking.** Give the entity a nullable `origin_source_id`, or add an `intersphinx_entity_source` claim table, and teach `prune_orphan_entities` to keep entities claimed by a live source. This separates "who says this exists" from "who documents it", which is the distinction the two models actually disagree about, and it generalizes: a Sphinx site's undocumented-but-referenced parent packages are the same shape of thing.
3. **Keep the `sdm_*` tables as the definition store** and let convergence cover only the link side. Smallest blast radius, but it leaves two entity models in the codebase, which is the thing convergence was for.

Option 2 is the one that makes the model actually generic; option 1 is the one that ships fastest. Genuinely open.

## 8. Two smaller things convergence would fix

- **The timestamp-window sweep goes away.** `SdmSchemasStore.update_schema` deletes stale rows by `date_updated < now - 2 seconds`. An ingest slow enough to straddle that window deletes rows it just wrote. The intersphinx path's per-source replace plus recursive-CTE prune has no such window, and it is scoped to one source, so it is a correctness improvement and not only a refactor.
- **The `sphinx_domain` column is misnamed for a generic model.** An SDM entity is not declared in a Sphinx domain; storing `'sdm'` there is a lie the schema tells about itself. If the SDM domain converges, the column wants to be `link_domain` or `entity_domain` — a mechanical rename touching `src/`, `alembic/`, and `tests/`, best done as its own commit at the head of the follow-up rather than tangled into the data migration. (Note that `intersphinx_entity`, `links_intersphinx`, and `intersphinx_source` are then misnamed too; whether to rename the tables as well is a cost/benefit call the PRD should make explicitly, since the table renames touch every migration reader while the column rename touches only code.)

## 9. Sketch of a staged follow-up

Ordered so each stage is independently reviewable and none is a big-bang cutover. The PRD interview owns the real breakdown; this is a starting shape.

1. **Answer §2.5 option 1 first.** Check whether `sdm-schemas.lsst.io` publishes an `objects.inv` whose anchors match the Felis-ID anchors. A yes collapses most of what follows.
2. **Decide §7.** Everything downstream depends on whether definition and linkage are separate concepts.
3. **Rename `sphinx_domain`** → `link_domain` (§8), alone, mechanically.
4. **Add `sibling_sort_key`** (§3.1) and generalize the sibling cursor from `SdmColumnLinksCollectionCursor` (§4.1). `py` behavior is unchanged; nothing SDM has moved yet.
5. **Pin the name collation** to `C` (§4.3) with its index, and add the ingest-time assertion that names contain no dots and no characters below `.` (§2.1).
6. **Register SDM as a source** in whichever shape §2.5 resolved to, and add the per-domain link-title strategy (§2.4).
7. **Backfill** `sdm_*` into `intersphinx_entity` / `links_intersphinx`, serving the existing SDM URLs from the new store while the old tables stay in place and unread.
8. **Drop** `links_sdm_schemas`, `links_sdm_tables`, `links_sdm_columns`, and — pending §7 — `sdm_schema`, `sdm_table`, `sdm_column`.

Note that the SDM **URL surface does not have to converge with the storage.** `/links/domains/sdm/schemas/{s}/tables/{t}/columns/{c}` can be served from the generic store by joining its path segments with dots into a `name`, and `/links/domains/python/objects/{name}` takes the dotted name directly. Keeping the SDM routes is free; adding python-shaped `objects` / `children` routes to the `sdm` domain is a separate, optional API change that should not be smuggled into a storage migration.

## 10. Open questions for the PRD

1. Does `sdm-schemas.lsst.io` publish an `objects.inv`, and do its anchors match the Felis-ID anchors currently used? (§2.5)
2. Definition vs. linkage — which of §7's three options?
3. Keep `description` / `datatype` / `ivoa_ucd` / `ivoa_unit` in `extras`, or drop them? (§6)
4. Rename the `intersphinx_*` tables along with the `sphinx_domain` column, or only the column? (§8)
5. Is breaking in-flight SDM pagination cursors at the cutover acceptable? (§4.4)
6. Does anything outside Ook read the `sdm_*` tables directly? Nothing inside Ook does, but that is a weaker guarantee than it sounds for a shared database.
