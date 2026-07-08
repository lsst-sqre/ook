### Backwards-incompatible changes

- `POST /ingest/resources/documents` now returns a list of per-item ingest results instead of a bare list of document resources. Each result reports the document's `handle`, a `status` of `created`, `updated`, or `failed`, the stored `resource` (on success), and an `error` detail (on failure).

### Bug fixes

- Document ingest now processes each document in its own savepoint and reports its outcome individually. A single malformed document no longer 500s the whole batch or is silently dropped: it is reported as `failed` with an error detail while the remaining documents are still `created` or `updated`. The `created` vs `updated` distinction reuses the natural-key resolution so a document matched to an existing resource reports `updated`.
