##########
Change log
##########

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
