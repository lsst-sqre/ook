# Change log

<!-- scriv-insert-here -->

<a id='changelog-0.17.0'></a>

## 0.17.0 (2025-07-15)

### Backwards-incompatible changes

- Dropped the `collaboration` table from the database schema and removed related code from the application. Originally in Ook we wanted to treat human authors separate from pseudo authors in order to make building out a staff directory easier. However, working against the grain of authordb.yaml (the canonical source for Rubin author data) has proven to be difficult. Now collaborations/collective authors will appear in the `/authors/` endpoints and in the `author` database table.

- Requires database migration, `113ced7d2d29`.

<a id='changelog-0.16.0'></a>

## 0.16.0 (2025-07-11)

### New features

- Handle the parsing exception when a LaTeX (Lander) document's articleBody metadata is still LaTeX-formatted rather than the excepted Markdown conversion. The metadata parser still creates a content chunk for Algolia consisting of the title and description/abstract.

- Handle parsing Technote (Sphinx) technotes where the abstract directive is missing. The metadata parser now returns a default message indicating that the abstract is not available.

### Other changes

- Adopt nox-uv for installing dependencies in `noxfile.py`.

<a id='changelog-0.15.0'></a>

## 0.15.0 (2025-07-07)

### Backwards-incompatible changes

- The author resources in the REST API have the following changes:

  - The `surname` field is now `family_name` to better match common usage.

  - The affiliation metadata is no longer a simple string, but instead a structured object with address components.

- A database migration is required (Alembic migration `176f421b2597`).

### New features

- In addition to the backwards-incompatible changes related to the author `family_name` field and affiliation `address`, the authors API now includes the ROR ID for affiliations and the department name for an affiliation, where appropriate. Ook now reflects the structure of [lsst/lsst-texmf](https://github.com/lsst/lsst-texmf)'s `authordb.yaml` file as of 2025-07-05.

<a id='changelog-0.14.0'></a>

## 0.14.0 (2025-06-23)

### Backwards-incompatible changes

- Changed the `GET /authors/id/{id}` endpoint to now be `GET /authors/{id}` to align with the other endpoints in the API.

- Changed SQL table names to be singular instead of plural. This change requires a database migration (Alembic migration `fb5ed49d63d5`).

### New features

- The `POST /ingest/lsst-texmf` endpoint (and `ook ingest-lsst-texmf` command) provides an option to delete author records that are no longer present in `authordb.yaml`. This is not the default behavior.

### Bug fixes

- Collaborations are now filtered out from the `/authors` endpoint. We may add a new collaborations endpoint in the future.

- Terms in `glossarydefs.csv` are deduplicated before being added to the database. This prevents duplicate terms in the CSV, a common typo, from preventing the ingestion of the glossary definitions.

### Other changes

- Dropped the `nox init`, `init-venv`, and `update-deps` sessions in favor of Makefile targets to reduce subtle issues about how `nox` depends on `uv` in the `nox` context.

<a id='changelog-0.13.1'></a>

## 0.13.1 (2025-04-30)

### Bug fixes

- The database session is now committed after running `ook ingest-lsst-texmf`.

<a id='changelog-0.13.0'></a>

## 0.13.0 (2025-04-30)

### New features

- Added a new Author API to interact with author metadata records from Rubin Observatory's author database, which is canonically maintained as the `etc/authordb.yaml` file in [lsst/lsst-texmf](https://github.com/lsst/lsst-texmf).

  - Use the new endpoint `GET /ook/authors` to paginate over all author records. Author records include affiliations.

  - Use `GET /ook/authors/id/{internal_id}` to retrieve the record for a single author based on their author ID.

- Added a Glossary API to interact with the Rubin Observatory glossary, which is canonically maintained in the `etc/glossarydefs.csv` and `etc/glossarydefs_es.csv` files in [lsst/lsst-texmf](https://github.com/lsst/lsst-texmf).

  - The `GET /ook/glossary/search?q={term}` endpoint allows searching for glossary terms. The search is case-insensitive and typo-tolerant.

- A new ingest endpoint, `POST /ook/ingest/lsst-texmf` triggers a refresh of author and glossary data from the `lsst/lsst-texmf` repository. This service can also be run from the CLI with the `ook ingest-lsst-texmf` command (useful for testing or cron jobs).

### Bug fixes

- Fixed the AsyncAPI documentation generation (available at `/ook/asyncapi`).

### Other changes

- Migrated dependency management to UV lockfiles, with dependencies defined in pyproject.toml's `dependencies` array and `dependency-groups` table. In addition to deleting the old `requirements/` files, this change also affects the Dockerfile, GitHub Actions, and Nox setup (`noxfile.py`).

- Adopt Python 3.13.

- Fixed the process for creating Alembic migrations, ensuring that the previous database schema is mounted correctly.

- The FastStream lifecycle is no longer explicitly managed.

<a id='changelog-0.12.0'></a>

## 0.12.0 (2025-04-16)

### New features

- The Links API collection endpoints now use pagination for improved performance and usability. Ook uses keyset pagination, so look for a Links header with `next`, `prev`, and `first` links. Use these URLs to advance to the next page. The `X-Total-Count` header indicates the total number of items in the collection. Pagination applies to the following endpoints:

  - `GET /ook/links/domains/sdm/schemas`
  - `GET /ook/links/domains/sdm/schemas/:schema/tables`
  - `GET /ook/links/domains/sdm/schemas/:schema/tables/:table/columns`

<a id='changelog-0.11.0'></a>

## 0.11.0 (2025-04-04)

### New features

- New Links API, available at `/ook/links`, that provides documentation links to Observatory and survey entities across different domains. This Links API is described in [SQR-086](https://sqr-086.lsst.io). Initially the Links API supports links to documentation about the Science Domain Model (SDM) schemas, tables, and columns.
- A new endpoint, `/ook/ingest/sdm-schemas` triggers an ingest of links for schema, table, and column entities in the https://github.com/lsst/sdm_schemas repository to targets in https://sdm-schemas.lsst.io. This endpoint is being developed towards the creation of a links API service, see [SQR-086](https://sqr-086.lsst.io).

### Other changes

- Adopt Faststream 0.5, dropping an earlier pin on Faststream 0.4.
- Adopt UV in the Docker build.
- Ook now uses a Postgres database to maintain datasets. Initially Postgres tables are used to store the SDM schemas as well as links for the Links API. The Postgres database is managed by Alembic, and the database schema is maintained with SQLAlchemy. The `OOK_DATABASE_URL` and `OOK_DATABASE_PASSWORD` environment variables configure the connection to this database.
- The nox `run` session can now run with roundtable-dev credentials from 1Password for testing the application locally. See `square.env` for details.

<a id='changelog-0.10.0'></a>

## 0.10.0 (2024-08-14)

### New features

- Ook now uses [faststream](https://faststream.airt.ai/latest/) for managing its Kafka consumer and producer. This is also how the Squarebot ecosystem operates. With this change, Ook no longer uses the Confluent Schema Registry. Schemas are instead developed as Pydantic models.

### Other changes

- Use `uv` for installing and compiling dependencies in `noxfile.py`.
- Update GitHub Actions workflows to use the [lsst-sqre/run-nox](https://github.com/lsst-sqre/run-nox) GitHub Action.
- Adopt `ruff-shared.toml` for shared Ruff configuration (from https://github.com/lsst/templates)
- Update Docker base to Python 3.12.5-slim-bookworm.
- Switch to [testcontainers](https://testcontainers.com) for running Kafka during test sessions. The Kafka brokers is automatically started by the `nox` sessions.

<a id='changelog-0.9.1'></a>

## 0.9.1 (2024-01-29)

### Bug fixes

- If a technote doesn't have the `og:article:modified_time` then Ook falls back to using the current time of ingest. This fallback is to meet the schema for the www.lsst.io website, and ideally documents should always set modification time metadata.

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
