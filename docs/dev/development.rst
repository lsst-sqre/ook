#################
Development guide
#################

This page provides procedures and guidelines for developing and contributing to Ook.

Scope of contributions
======================

Ook is an open source package, meaning that you can contribute to Ook itself, or fork Squarebot for your own purposes.

Since Ook is intended for internal use by Rubin Observatory, community contributions can only be accepted if they align with Rubin Observatory's aims.
For that reason, it's a good idea to propose changes with a new `GitHub issue`_ before investing time in making a pull request.

Ook is developed by the Rubin Observatory SQuaRE team.

.. _GitHub issue: https://github.com/lsst-sqre/ook/issues/new

.. _dev-environment:

Setting up a local development environment
==========================================

Local development requires:

- Python 3.14
- Uv_
- Docker or a compatible container runtime

First, clone the Ook repository:

.. code-block:: sh

   git clone https://github.com/lsst-sqre/ook.git
   cd ook

Ook is a Python project that should be developed within a virtual environment.
Create this virtual environment with UV:

.. code-block:: sh

   uv venv
   source .venv/bin/activate

Then install the development dependencies and pre-commit hooks:

.. code-block:: sh

   make init

.. _pre-commit-hooks:

Pre-commit hooks
================

The pre-commit hooks ensure that files are valid and properly formatted.
The hooks are automatically installed by running the :command:`make init`.

When these hooks fail, your Git commit will be aborted.
To proceed, stage the new modifications and proceed with your Git commit.

Refer to the :file:`.pre-commit-config.yaml` file for the list of hooks that are run.

.. _dev-run-tests:

Running tests
=============

To test all components of Ook, run nox_, which tests the library the same way that the GitHub Actions CI workflow does.

.. tab-set::

   .. tab-item:: In venv
      :sync: venv

      .. code-block:: sh

         nox

   .. tab-item:: Without pre-installation
      :sync: uv

      .. code-block:: sh

         uv run --group=nox nox

File linting, type checking, and unit tests are run as separate nox sessions:

.. tab-set::

   .. tab-item:: In venv
      :sync: venv

      .. code-block:: sh

         nox -s lint
         nox -s typing
         nox -s unit

   .. tab-item:: Without pre-installation
      :sync: uv

      .. code-block:: sh

         uv run --group=nox nox -s lint
         uv run --group=nox nox -s typing
         uv run --group=nox nox -s unit

With unit tests, you can run a specific test file or directory:

.. tab-set::

   .. tab-item:: In venv
      :sync: venv

      .. code-block:: sh

         nox -s test -- tests/foo_test.py

   .. tab-item:: Without pre-installation
      :sync: uv

      .. code-block:: sh

         uv run --group=nox nox -s test -- tests/foo_test.py

To see a listing of specific nox sessions, run:

.. tab-set::

   .. tab-item:: In venv
      :sync: venv

      .. code-block:: sh

         nox --list

   .. tab-item:: Without pre-installation
      :sync: uv

      .. code-block:: sh

         uv run --group=nox nox --list


Building documentation
======================

Documentation is built with Sphinx_:

.. _Sphinx: https://www.sphinx-doc.org/en/master/


.. tab-set::

   .. tab-item:: In venv
      :sync: venv

      .. code-block:: sh

         nox -s docs

   .. tab-item:: Without pre-installation
      :sync: uv

      .. code-block:: sh

         uv run --group=nox nox -s docs

The built documentation is located in the :file:`docs/_build/html` directory.

To check the documentation for broken links, run:

.. tab-set::

   .. tab-item:: In venv
      :sync: venv

      .. code-block:: sh

         nox -s docs-linkcheck

   .. tab-item:: Without pre-installation
      :sync: uv

      .. code-block:: sh

         uv run --group=nox nox -s docs-linkcheck

.. _dev-change-log:

Updating the change log
=======================

Ook uses scriv_ to maintain its change log.

When preparing a pull request, run

.. tab-set::

   .. tab-item:: In venv
      :sync: venv

      .. code-block:: sh

         nox -s scriv-create

   .. tab-item:: Without pre-installation
      :sync: uv

      .. code-block:: sh

         uv run --group=nox nox -s scriv-create

This will create a change log fragment in :file:`changelog.d`.
Edit that fragment, removing the sections that do not apply and adding entries for your pull request.

Change log entries use the following sections:

- **Backward-incompatible changes**
- **New features**
- **Bug fixes**
- **Other changes** (for minor, patch-level changes that are not bug fixes, such as logging formatting changes or updates to the documentation)

Do not include a change log entry solely for updating pinned dependencies, without any visible change to Ook's behavior.
Every release is implicitly assumed to update all pinned dependencies.

These entries will eventually be cut and pasted into the release description for the next release, so the Markdown for the change descriptions must be compatible with GitHub's Markdown conventions for the release description.
Specifically:

- Each bullet point should be entirely on one line, even if it contains multiple sentences.
  This is an exception to the normal documentation convention of a newline after each sentence.
  Unfortunately, GitHub interprets those newlines as hard line breaks, so they would result in an ugly release description.
- Avoid using too much complex markup, such as nested bullet lists, since the formatting in the GitHub release description may not be what you expect and manually editing it is tedious.

.. _dev-outbound-http:

Outbound HTTP and the SSRF guard
================================

Ook fetches URLs it did not choose: the link checker requests every external URL a documentation build submits, and the intersphinx inventory cache fetches whatever ``objects.inv`` URL a client asks for.
Both run inside the cluster, so an unguarded fetch could be steered by a submitted URL, or by a redirect its server returns, at internal services, the cloud metadata endpoint, or anything else reachable from Ook's pod: a server-side request forgery (SSRF).
Every such fetch goes through the ``ook.services.guardedhttp`` package, Ook's single resolve-validate-pin implementation.
The `DM-56836 research report`_ has the reproduction and the httpcore analysis behind its design.

.. _DM-56836 research report: https://claude.ai/artifact/1CXMu7bsM6TMNmLjvUcT4Q

Guard layers
------------

Every request passes four layers, and every redirect hop passes them again:

#. **Scheme and shape.**
   Each service first refuses a URL it never fetches.
   The link checker accepts only well-formed ``http`` and ``https`` URLs and reports anything else as ``unsupported``; the intersphinx cache accepts only well-formed ``https`` URLs and answers anything else with a 400.

#. **Resolve and validate.**
   ``resolve_and_validate`` validates an IP literal directly and resolves a hostname with ``getaddrinfo``.
   It refuses the host unless *every* address it resolves to is globally routable, because the connection could otherwise be made to any of them, and it judges an IPv4-mapped IPv6 address (``::ffff:10.0.0.1``) by the IPv4 address it embeds.
   Its two refusals mean different things.
   ``NonPublicHostError`` (a private, loopback, link-local, unique-local, or reserved address) is a fact about the URL, so the link checker reports ``unsupported`` and the cache answers 400.
   ``HostResolutionError`` (the host does not resolve) is an upstream condition that may clear by itself, so the link checker keeps the URL on its retry ladder and the cache answers a negatively cached 502.

#. **Per-hop re-guarding.**
   Neither service lets httpx follow redirects.
   Each walks a redirect chain itself, one hop at a time up to 20 hops, and guards every ``Location`` before requesting it, so a public URL cannot redirect Ook to an internal one.
   The intersphinx cache resolves a host only once per chain, pinning a hop back to a host it already validated in that chain to the address it validated then.
   The upstream chose that hop, not the client, so the cache answers a refused hop with a 502 rather than a 400.

#. **Socket pinning.**
   The socket connects to exactly the address the guard validated, not to whatever the hostname resolves to a moment later.
   This closes the DNS-rebinding gap between the guard's lookup and the connect, in which a short-lived or hostile DNS answer hands the guard a public address and the connect a private one.

The guarded client
------------------

``ProcessContext`` holds two HTTP clients, and every outbound request belongs on exactly one of them:

``guarded_http_client``
   Built by ``create_guarded_client``, for URLs that a user, a client, or an upstream response supplied.
   Its connection pool (``GuardedTransport``) opens sockets through ``PinningNetworkBackend``, an httpcore network backend that dials the guard-validated address.
   The URL checker (``UrlChecker``) and the intersphinx inventory cache (``IntersphinxCacheService``) use it; the intersphinx ingest pipeline fetches only through the cache.

``http_client``
   A plain ``httpx.AsyncClient`` for URLs Ook chooses itself: GitHub OIDC and JWKS verification, LTD, technote metadata, and the like.

``create_guarded_client`` refuses the ``transport``, ``mounts``, and ``proxy`` options, any of which would route requests around the guarded transport, and leaves HTTP/2 off, so there is no connection coalescing across hostnames either.

The caller hands each validated address to the network backend through a :py:class:`contextvars.ContextVar`.
A service validates a hop's host and makes that hop's request inside ``pinned_to``, which records host → address for the duration of the block:

.. code-block:: python

   address = await resolve_and_validate(url.host)
   with pinned_to(url.host, address):
       response = await guarded_http_client.get(url)

When httpcore opens a new connection inside the block, ``PinningNetworkBackend.connect_tcp`` looks up the host it was given and dials the pinned address instead of resolving the hostname again.
Each asyncio task has its own context, so concurrent requests on the shared client never see each other's pins.
``pinned_to`` itself refuses an address that is not a public IP literal, so a caller cannot pin a socket somewhere the guard would not accept.

The backend fails closed.
A request that reaches ``connect_tcp`` with no address pinned for its host is guarded by the backend itself: it runs ``resolve_and_validate`` within the connect timeout and then dials the address it validated, or refuses with ``GuardRefusedConnectError``, which the caller sees as an ``httpx.ConnectError``.
No connection through the guarded client is ever made unguarded.
The fallback means a caller skipped its own pre-flight guard, so it logs a warning; treat that warning as a bug in the caller.

Why the connection pool must be keyed by hostname
-------------------------------------------------

httpcore reuses a pooled keep-alive connection for any request whose scheme, host, and port match the ones the connection was opened for.
It reads the TLS server name only when it opens a new socket, so a reused connection keeps the SNI and certificate it was opened with.

Ook 0.23.0 first pinned link-check sockets by rewriting each request URL's host to the validated IP, restoring the hostname with an explicit ``Host`` header and the ``sni_hostname`` request extension.
That keyed the pool by IP.
Fastly serves docs.python.org and pypi.org from the same addresses with different certificates, so once the checker had requested pypi.org, a request for a docs.python.org page rode the pypi.org connection and Fastly refused it with a 421 Misdirected Request (DM-56836).
On Cloudflare the same collision came back as a 403, which the checker reported as ``blocked``.

The guarded client leaves the hostname in the request URL and moves the pin below the pool, into the network backend.
TLS SNI and certificate verification use the hostname with no override, and the pool is keyed by hostname, so a connection is only ever reused for the hostname it was opened for: two hostnames pinned to one address get two connections.
As a backstop, the link checker treats any 421 as inconclusive (``blocked``) rather than as evidence that a link is broken.

From this follows the rule for any outbound request: **no service rewrites a URL host to an IP.**
Pin the socket with ``pinned_to`` instead, and never add a ``Host`` header override or the ``sni_hostname`` extension to compensate for a rewritten URL.

The module docstrings carry the details:

- :file:`src/ook/services/guardedhttp/__init__.py`: the layers and the hostname rule.
- :file:`src/ook/services/guardedhttp/_guard.py`: ``resolve_and_validate`` and its two refusals.
- :file:`src/ook/services/guardedhttp/_pinning.py`: ``pinned_to`` and ``PinningNetworkBackend``, including the fail-closed fallback.
- :file:`src/ook/services/guardedhttp/_transport.py`: ``GuardedTransport`` and ``create_guarded_client``.
- ``UrlChecker`` in :file:`src/ook/services/linkcheck/_urlchecker.py` and ``IntersphinxCacheService`` in :file:`src/ook/services/intersphinx.py`: how each service maps the guard's refusals onto its own outcomes.

.. _style-guide:

Style guide
===========

Code
----

- The code style follows :pep:`8`, though in practice lean on Black and ruff to format the code for you. Use :sqr:`072` for for architectural guidance.

- Use :pep:`484` type annotations.
  The ``nox -s typing`` test session, which runs mypy_, ensures that the project's types are consistent.

- Write tests for Pytest_.

.. _style-guide-http-api:

HTTP API
--------

- Name every date-valued field in a request or response body with a ``date_`` prefix.
  Examples across Ook's API are ``date_created``, ``date_checked``, ``date_next_check``, and ``date_fetched``.
  Prefer a specific suffix (``date_next_check``) over a bare ``date``, and do not use an ``_at`` or ``_timestamp`` suffix instead.
  This covers bare calendar dates (:py:class:`datetime.date`) as well as timestamps (:py:class:`datetime.datetime`), since the distinction is one of precision rather than of kind.

- Response headers Ook defines follow the same convention, written as a capitalized segment.
  The intersphinx inventory endpoint, for example, reports the age of its cached copy in ``X-Ook-Inventory-Date-Fetched``.

- The one exception is a field or header that carries a **verbatim HTTP header value** rather than a parsed timestamp.
  Such a field mirrors the wire name instead, and is typed as the string it holds rather than as a :py:class:`~datetime.datetime`.
  The intersphinx domain's ``last_modified`` is the standing example: it holds the upstream ``Last-Modified`` header exactly as received, so that Ook can echo it back in a conditional ``If-Modified-Since`` revalidation.

- Publish every identifier that a request or response body, a path parameter, or a self URL carries as a Crockford Base32 ID from :file:`src/ook/domain/base32id.py`.
  The Pydantic field is typed ``Base32Id`` (or its ``Base32IdNoHyphens`` and ``Base32IdShort`` siblings), which validates the checksummed string on the way in and serializes the stored integer back out.
  The ID is minted by Ook rather than by PostgreSQL, so the column that holds it is declared ``autoincrement=False``.
  See the "Resource ID Format" section of :file:`planning/bibliography-api.md` for why the API publishes Base32 strings rather than integers.

- Mint the ID with ``generate_resource_id`` wherever its order is meaningful, which is the case for resources and for anything else paginated by a keyset cursor over the ID.
  That helper mints a time-ordered ID, so plain integer ordering is creation order.
  Mint it with ``generate_base32_id`` instead for a small registry that is never ordered by ID, such as the linkcheck checks and the intersphinx source registry.

- An autoincrement integer primary key stays internal, and is the right choice for a table whose key never leaves the storage layer.
  Join tables, entities the API addresses by name, and cache rows keyed by their URL are all in that category.

- An identifier that is genuinely a string upstream keeps its own type rather than being re-minted.
  The authors' ``internal_id`` and the linkcheck contribution's GitHub ``run_id`` are the standing examples.

Both conventions are enforced by :file:`tests/api_field_naming_test.py`.
That test introspects every Pydantic model defined under :file:`src/ook/handlers`, resolving ``Annotated[...]`` wrappers, optional unions, and :pep:`695` type aliases, and fails with the offending ``module.Model.field`` when a date-valued field lacks the ``date_`` prefix or an ``id`` or ``*_id`` field is typed as a plain :py:class:`int`.
It is pure introspection, so it needs no database or application fixture and runs in the containers-free ``test-unit`` session.

Documentation
-------------

- Follow the `LSST DM User Documentation Style Guide`_, which is primarily based on the `Google Developer Style Guide`_.

- Document the Python API with numpydoc-formatted docstrings.
  See the `LSST DM Docstring Style Guide`_.

- Follow the `LSST DM ReStructuredTextStyle Guide`_.
  In particular, ensure that prose is written **one-sentence-per-line** for better Git diffs.

.. _`LSST DM User Documentation Style Guide`: https://developer.lsst.io/user-docs/index.html
.. _`Google Developer Style Guide`: https://developers.google.com/style/
.. _`LSST DM Docstring Style Guide`: https://developer.lsst.io/python/style.html
.. _`LSST DM ReStructuredTextStyle Guide`: https://developer.lsst.io/restructuredtext/style.html
