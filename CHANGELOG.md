# Change log

<!-- scriv-insert-here -->

<a id='changelog-0.9.0'></a>
## 0.9.0 (2023-09-26)

### New features

- Added support for ingesting Technotes (as generated with the technote.lsst.io framework). These technotes are generated with Sphinx, but embed metadata in common formats like Highwire Press and OpenGraph. This new technote format replaces the original technote format, although the original technotes are still supported by Ook.

<a id='changelog-0.8.0'></a>
## 0.8.0 (2023-09-06)

### New features

- Add a new `ook ingest-updated` command to queue ingest tasks for all LTD projects that have updated within a specified time period. This command is intended to be run as a Kubernetes cron job. Once push-based queueing from LTD is available on the roundtable-prod Kubernetes cluster this command can be deprecated.

<a id='changelog-0.7.1'></a>
## 0.7.1 (2023-09-05)

### Bug fixes

- Improved and logging and exception reporting around the `ook audit` command.
- Fixed the `base_url` attribute's JSON alias for the Algolia DocumentRecord model. Was `baseURL` and is now restored to `baseUrl`.
- Fix typo in creating records for Lander content types (`source_update_time` and `source_update_timestamp` fields).

<a id='changelog-0.7.0'></a>
## 0.7.0 (2023-08-31)

### New features

- The new `ook audit` command (and associated `AlgoliaAuditService`) audits the contents of the Algolia index to determine if all documents registered in the _LSST the Docs_ API are represented in the Algolia index. This command can be run as `ook audit --reingest` to automatically queue reingestion jobs for any missing documents.

### Bug fixes

- Fixed the CLI entrypoint from `squarebot` to `ook`.

### Other changes

- The Factory is refactored. A `ProcessContext` now holds singleton clients for the duration of the process, and is used for both the API handlers and for worker processes, including CLI instantiations of Ook as Kubernetes jobs. This new architecture moves configuration of Kubernetes and registration of Kafka Avro schemas out of the main module and into the factory instantiation.
- The Algolia search client is now mocked for testing. This allows the new factory to always create a search client for the process context. It also means that Algolia client credentials are always required; the test configuration uses substitute keys for the mock.

## 0.6.0 (2023-07-20)

### Backwards-incompatible changes

- The app is rewritten as a FastAPI/Safir app, replacing its heritage as an aiohttp/Safir app. The app is also now deployed with Helm via [Phalanx](https://phalanx.lsst.io) Because of this, Ook should be considered as an entirely new app, with no backwards compatibility with the previous version.
- Ook no longer receives GitHub webhooks; the intent is to get GitHub webhook events from Squarebot (through Kafka) in the future.
- Ook no longer receives Kafka messages from LTD Events since that app isn't avabile in the new Roundtable deployment. A new ingest trigger is being developed in the interim. Until then, ingests can be manually triggered by the `POST /ook/ingest/ltd` endpoint.

### New features

- Ook is now a FastAPI/Safir app.
- Ook uses Pydantic models for its Kafka message schemas.
- Ook is now built around a service/domain/handler architecture, bringing it in line with SQuaRE's modern apps. The Kafka consumer is considered a handler.
- Add `ook upload-doc-stub` CLI command to manually add a single record to Algolia to stub a document into the www.lsst.io search index. This is useful for cases where a document can't be normally indexed by Ook.

### Other changes

- The change log is maintained with scriv
- Tests are now orchestrated through nox.

## 0.5.0 (2021-12-01)

### New features

- Compatibility with "main" as the default branch when sorting and detecting `technote` metadata.yaml files.

## 0.4.0 (2021-09-13)

### New features

- Documents are ingested with a new `sourceCreationTimestamp`.
  This timestamp corresponds to the time when a document was initially created.
  A new workflow, `get_github_creation_date` can be used to infer this creation date on the basis of the first GitHub commit on the default branch that was not made by `SQuaRE Bot` (or any email/name corresponding to a bot) during the initial template instantiation.

- Ook is now configured as a GitHub App.

## 0.3.1 (2021-03-02)

### Bug fixes

- Added hardening to the Kubernetes deployment manifests

## 0.3.0 (2020-07-17)

### New features

- Improved ingest reliability:

  - For Lander (PDF) content, added a heuristic that rejects TeX that Pandoc might let through.
  - Handle AASTeX technotes that don't have full Lander site.
    Specifically, AASTeX technotes don't include the handle in the TeX source, so instead we use the document's URL.
  - If a Lander site doesn't have an abstract, we fall back to using the first content chunk.
  - Support Lander docs without content
  - Support Sphinx technotes that include content before the first subsection header.

- Improved logging during ingests, including logging of records when an insertion into the Algolia index fails.

### 0.2.0 (2020-07-02)

### New features

- Support for sorting documents:

  - The `number` record field is now numeric, supporting sortable document handles.

  - The new `sourceUpdateTimestamp` is the integer Unix timestamp corresponding to when the document was updated.
    This timestamp supports sorting documents by their update recency.

- After ingest, old records for a URL are deleted.
  This expiration is done by searching for records for a given `baseUrl` that have a `surrogateKey` value other than that of the current ingest.

- In development environments, `make test` now runs Ook through its tox configuration.

- Refreshed all pinned dependencies

## 0.1.0 (2020-06-18)

### New features

- First release of Ook!

  This release includes support for classifying and ingesting both Lander-based PDF documents with JSON-LD metadata and Sphinx/ReStructuredText-based HTML technotes.

  This release also includes a full Kustomize-based Kubernetes deployment manifest.
