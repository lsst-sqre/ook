# Change log

<!-- scriv-insert-here -->

## Unreleased

### New features

- Add `ook upload-doc-stub` CLI command to manually add a single record to Algolia to stub a document into the www.lsst.io search index. This is useful for cases where a document can't be normally indexed by Ook.

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
