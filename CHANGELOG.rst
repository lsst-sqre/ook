##########
Change log
##########

0.3.1 (2021-03-02)
==================

- Added hardening to the Kubernetes deployment manifests

- Refreshed all pinned dependencies

0.3.0 (2020-07-17)
==================

- Improved ingest reliability:

  - For Lander_ (PDF) content, added a heuristic that rejects TeX that Pandoc might let through.
  - Handle AASTeX technotes that don't have full Lander_ site.
    Specifically, AASTeX technotes don't include the handle in the TeX source, so instead we use the document's URL.
  - If a Lander_ site doesn't have an abstract, we fall back to using the first content chunk.
  - Support Lander_ docs without content
  - Support Sphinx technotes that include content before the first subsection header.

- Improved logging during ingests, including logging of records when an insertion into the Algolia index fails.

- Refreshed all pinned dependencies

0.2.0 (2020-07-02)
==================

- Support for sorting documents:

  - The ``number`` record field is now numeric, supporting sortable document handles.

  - The new ``sourceUpdateTimestamp`` is the integer Unix timestamp corresponding to when the document was updated.
    This timestamp supports sorting documents by their update recency.

- After ingest, old records for a URL are deleted.
  This expiration is done by searching for records for a given ``baseUrl`` that have a ``surrogateKey`` value other than that of the current ingest.

- In development environments, ``make test`` now runs Ook through its tox configuration.

- Refreshed all pinned dependencies

0.1.0 (2020-06-18)
==================

- First release of Ook!

  This release includes support for classifying and ingesting both Lander_\ -based PDF documents with JSON-LD metadata and Sphinx/ReStructuredText-based HTML technotes.

  This release also includes a full Kustomize-based Kubernetes deployment manifest.

.. _Lander: https://github.com/lsst-sqre/lander
