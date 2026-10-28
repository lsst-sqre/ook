.. _dev-outbound-http:

################################
Outbound HTTP and the SSRF guard
################################

Ook fetches URLs it did not choose: the link checker requests every external URL a documentation build submits, and the intersphinx inventory cache fetches whatever ``objects.inv`` URL a client asks for.
Both run inside the cluster, so an unguarded fetch could be steered by a submitted URL, or by a redirect its server returns, at internal services, the cloud metadata endpoint, or anything else reachable from Ook's pod: a server-side request forgery (SSRF).
Every such fetch goes through the ``ook.services.guardedhttp`` package, Ook's single resolve-validate-pin implementation.
Its design, and the httpcore analysis behind it, are recorded in the PRD, `lsst-sqre/ook#426`_ (DM-56836); `Why the connection pool must be keyed by hostname`_ below summarizes them.

.. _lsst-sqre/ook#426: https://github.com/lsst-sqre/ook/issues/426

Guard layers
============

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
   Each new socket connects to exactly the address the guard validated, not to whatever the hostname resolves to a moment later.
   This closes the DNS-rebinding gap between the guard's lookup and the connect, in which a short-lived or hostile DNS answer hands the guard a public address and the connect a private one.
   The pin applies when a socket is opened: a request that reuses a pooled keep-alive connection for the same hostname opens no socket, so it rides one that was opened to an address the guard validated for an earlier request, and an idle connection leaves the pool once its keep-alive expiry (5 seconds by default) passes.

The guarded client
==================

``ProcessContext`` holds two HTTP clients, and every outbound request belongs on exactly one of them:

``guarded_http_client``
   Built by ``create_guarded_client``, for URLs that a user, a client, or an upstream response supplied.
   Its connection pool (``GuardedTransport``) opens sockets through ``PinningNetworkBackend``, an httpcore network backend that dials the guard-validated address.
   The URL checker (``UrlChecker``) and the intersphinx inventory cache (``IntersphinxCacheService``) use it; the intersphinx ingest pipeline fetches only through the cache.

``http_client``
   A plain ``httpx.AsyncClient`` for URLs Ook chooses itself: GitHub OIDC and JWKS verification, LTD, technote metadata, and the like.

``create_guarded_client`` refuses the ``transport``, ``mounts``, and ``proxy`` options, any of which would route requests around the guarded transport, and leaves HTTP/2 off, so there is no connection coalescing across hostnames either.
It also refuses ``verify``, ``cert``, ``trust_env``, ``http1``, and ``http2``: httpx applies those only to a transport it builds itself, so a client given the guarded transport would silently ignore them, while the guarded transport keeps the TLS defaults of ``httpx.create_ssl_context()`` and speaks HTTP/1.1 only.
Options httpx applies at the client layer, such as ``timeout``, ``headers``, and ``follow_redirects``, pass through.

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
=================================================

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
