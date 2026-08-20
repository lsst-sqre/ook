# Change log

<!-- scriv-insert-here -->

<a id='changelog-0.26.0'></a>
## 0.26.0 (2026-08-19)

### New features

- `GET /ook/authors` now takes an `orcid` query parameter that resolves an author's ORCID to their record, so a client holding an ORCID no longer has to run a fuzzy name search and match the ORCID against the results locally — a search that misses outright when the name is spelled differently enough. Because `author.orcid` is unique, an ORCID identifies at most one author: the response is the same `Author` array the bare listing returns, holding zero or one records, and an ORCID nobody holds is a `200` with `[]` rather than a `404`. The ORCID may be written bare (`0000-0003-3001-676X`), with a lowercase checksum character, or as an `orcid.org` URL. The `orcid` query pattern cannot be used in combination with search (doing so results in a `422` error.)

- `GET /ook/intersphinx/inventory` now reports some response headers with information about the served object inventory:

  - `X-Ook-Inventory-Permanent-Redirect` header names the URL the origin's redirect chain resolved to for a permanently-moved inventory URL. It is emitted on a `304` as well as a `200`, so a client that holds the current bytes and only ever revalidates still learns that the URL in its configuration is stale.

  - `X-Ook-Inventory-Date-Fetched` header gives an absolute RFC 3339 UTC timestamp the moment Ook last confirmed that inventory with its origin. It is the same freshness anchor `Age` counts from, stated outright instead of as a count of seconds back from now, and it rides the `304` as well as the `200` — `Age` rides the `200` alone — so a client that holds the current bytes and only ever revalidates can read the cache's behavior off each response rather than inferring it. The anchor is when the inventory was last *confirmed*, not when the served bytes were downloaded: a background refresh whose conditional revalidation is answered `304 Not Modified` keeps the stored bytes and advances it, which is exactly what `Age` has always reported. A cached row that records no fetch at all carries no header rather than a placeholder, deliberately unlike `Age`, which falls back to `0` on such a row and so reports a copy of unknown age as freshly fetched.

  - `X-Ook-Inventory-Cache-Status` header describes how the object inventory is served: `hit` (served from a cached copy fetched within the freshness TTL), `stale` (served from a cached copy fetched longer ago than that TTL — still a cache serve rather than an error, since a copy past its TTL is deliberately retained for availability while the background refresh job revalidates it, so it is read alongside `Age`), or `miss` (not cached when the request arrived, so the origin was fetched synchronously to answer it).

### Bug fixes

- The intersphinx inventory cache now follows redirects when fetching an origin `objects.inv`. Previously any redirecting inventory URL failed as an upstream error, was negatively cached, and was served as a 502 to every client for the whole negative-TTL window — which made inventories such as SQLAlchemy's (a three-hop cross-host 302 chain), Pydantic's, and Sphinx's own permanently uncacheable. Redirects are followed by hand rather than by httpx, so the SSRF guard runs against every hop target before it is fetched and cannot be bypassed by an upstream `Location`; a relative `Location` is resolved against the hop that sent it; the chain is capped at 20 hops (matching the link checker), enforced on the hop count alone before the target that exceeds it is joined, guarded, or resolved, so an over-long chain always reports `Exceeded 20 redirects` rather than whatever the 21st target — a URL the fetch was never going to request — happened to fail on; and a redirect hop's body is read and discarded under a small fixed cap, so only the terminal response counts against the size cap. Both the cold-miss and the proactive-refresh path follow chains. Every way a hop can fail funnels into one upstream-failure taxonomy — a chain over the hop cap, a hop the SSRF guard refuses, a hop host that will not resolve or whose label IDNA cannot encode, and a `Location` that no parser accepts (including the `httpx.InvalidURL` httpx raises from inside the request when it joins an over-long target itself, before any response comes back and so beyond the reach of Ook's own join, and which is not an `httpx.HTTPError`) — and is reported as a negatively cached 502 on the request path and a single skipped inventory on the refresh path, rather than as an unhandled 500 that re-walked the origin chain on every request or an abort of the whole refresh batch at a row that then sorted stalest-first on every subsequent run. Only a guard rejection of the URL the client actually asked for is still a 400. Cold-miss and refresh logs now name the terminal URL and the redirect hop count.

- Conditional revalidation of a redirecting intersphinx inventory is scoped to the terminal that minted its validators. A refresh replays the stored `ETag` and `Last-Modified`, but those were minted by the terminal of the *previous* chain, and an alias re-pointed at an older resource would answer a false `304` — leaving Ook serving the old version's bytes marked fresh while `resolved_url` (and the permanent-redirect header derived from it) named the new terminal, and, because the validators never changed, repeating the same false `304` on every subsequent run so it could never self-heal. The stored validators are therefore sent only to the terminal that minted them when the row records one; a chain landing anywhere else is asked unconditionally and has to answer with the body, so the new content, terminal, and hop record all come from that one fetch. A row that records no terminal — as every row cached before the `resolved_url` column existed does — sends them along the chain instead and records the terminal its `304` came from, so it is held to that terminal from the next refresh on; on such a row `If-Modified-Since` is dropped as soon as a hop redirects, since a modification date is only meaningful against the resource it was read from, while `If-None-Match` is kept (a strong validator stays trustworthy wherever the chain lands, and RFC 9110 §13.1.3 gives it precedence anyway). A `304` from the terminal that minted the validators still revalidates the stored copy in place, updating the resolved-chain columns from the chain that revalidation walked. Scoping the validators this way also keeps the refresh cheap for an origin whose `Location` carries a per-response token or a load-balancer shard and so never lands on the same terminal twice: its one request is answered with the body, rather than paying two chain walks and a full body transfer out of a single fetch budget on every run, forever.

- Bounded an intersphinx origin fetch as a whole, not just hop by hop, and enforced that bound by cancellation. The 30-second request timeout is now a single time budget for the entire fetch: it is taken when the fetch starts and covers the SSRF guard's host resolution for the requested (or stored) URL, every redirect hop's request and every hop target's guard resolution, and the terminal response's body read alike, with each hop's request additionally given no more than the time left and refused outright once the budget is spent. Previously an origin that answered each redirect just inside the per-request timeout could stretch one cold-miss fetch to the 20-hop cap times that timeout, plus a DNS resolution per hop with no timeout at all; and the budget was in any case only *checked* between redirect hops and between body chunks, otherwise handed down as per-call httpx timeouts — but httpcore re-arms the read timeout on every socket read, so an origin trickling response-header bytes kept a single hop alive far past the budget, and a hop's connect, write, and header-read phases each got the full remainder in turn. The guard on the requested URL was worse off still: it ran before the budget existed, so a hung resolver ladder — likely in a cluster with no caching resolver and `ndots` search expansion — blocked the request or stalled the serial refresh batch outside every bound the fetch had. The guard and the whole redirect chain are now wrapped in a single `asyncio.timeout` whose expiry maps to the spent-budget error: a negatively cached 502 on the request path, a per-inventory skip on the refresh path. Per-call httpx timeouts remain as belt and braces, and the recorded outcome of an exhausted budget is written outside the cancelled region so the failure is never lost. On the cold-miss path a hop holds the request's database session open for its whole duration, which is the connection-pool exhaustion hazard the budget exists to close.

- Backed off failed intersphinx refreshes instead of retrying them every run. A refresh failure is now recorded on the row — as `last_fetch_status`, `last_fetch_error`, and a new `date_refresh_failed` backoff marker, committed with the rest of the batch — and the refresh due-list holds an inventory back for one freshness TTL after its last failure, so a broken origin is retried on the normal refresh cadence. Previously a failed refresh wrote nothing, so the inventory kept its stale fetch time and was selected again on every single run for its whole 30-day active window, sorting stalest-first ahead of every healthy inventory and (now that a fetch follows redirects) spending a whole redirect chain on each futile attempt while the rest of the batch waited behind it. The failure write is guarded on the freshness anchor the due-list read saw, so a failure that lands after a client cold miss stored good content for the same URL is dropped instead of stamping a `failure` status, a stale error, and a backoff marker onto the fresh copy. It is also stamped when it is written rather than when the batch started — as is a successful fetch's `date_fetched` — so a row that fails deep into a long serial run backs off a full TTL from its own attempt rather than being back in the very next hourly run's due list. The failure write touches nothing else: content, its validators, its resolved-redirect columns, and the `date_fetched` freshness anchor are all left as the last successful fetch wrote them, so the stored copy keeps serving stale at its true reported age, and a subsequent successful refresh clears the marker.

- Guarded the intersphinx refresh job's success write against a row a client refreshed under it. The write was an unconditional UPDATE keyed on the URL alone that rewrote every outcome column — content included — from the snapshot the due-list read returned. A row in that list is stale by construction, so a client cold miss can fetch and commit good content while the refresh's own fetch is still in flight, and the refresh's write would then revert that content and stamp the reverted bytes fresh, hiding the regression for a whole TTL. Both the `200` and the `304` write are now guarded on the `date_fetched` the due-list read saw, matching the guards the negative-cache and refresh-failure writes already carried, and a dropped write is counted and reported as `superseded` rather than as a refresh that landed.

- Stopped rewriting an unchanged inventory body on a `304`. A successful revalidation rewrote the whole content blob it had read at due-list time, forcing a TOAST and WAL rewrite of the entire inventory per revalidated row per run — for a conditional request whose whole purpose is not to move the bytes. The `304` write now touches only the columns a revalidation actually changes: the fetch time, the fetch status and error, the resolved-redirect columns, and the backoff marker.

- Stopped trusting an intersphinx `304 Not Modified` that answered a request carrying no validator. A `304` asserts that the validator the client sent still matches, so one answering an unconditional request is about no cached copy at all — but the refresh path accepted it whenever the observed terminal matched the stored one. A negative-cache row (no content, no `ETag`, no `Last-Modified`) that aged back into the refresh due list therefore sent an unconditional GET, and an origin answering `304` to it was written back as a content-less *success* row: neither servable, since serving requires content, nor a live negative-cache entry, since that requires a failure status — and the write could clobber content a concurrent cold miss had just stored. A `304` is now accepted only when the request that drew it actually carried `If-None-Match` or `If-Modified-Since`, which also covers the case where the only stored validator was `If-Modified-Since` and the redirect chain dropped it. The check lives in the fetch itself, so it applies to the cold-miss fetch and to a refresh whose chain landed somewhere other than the terminal its validators are held to, as well as to an ordinary refresh, and the rejection is raised as a purpose-built upstream error with a detail naming the unconditional-request context, in place of the previous `raise_for_status()`-on-a-`304` idiom that worked only because httpx (unlike requests) raises on 3xx.

- Stopped a failed *bookkeeping* write from killing the whole intersphinx refresh run. Recording a per-inventory refresh failure is itself a guarded UPDATE plus a commit, and it races the very client cold miss that guard exists to defend against — so a serialization error, a deadlock, a statement timeout, or a connection recycled while a 30-second fetch held the session idle is expected contention rather than a remote possibility. Any of them propagated out of the handler whose whole purpose is that one inventory's failure never stops the batch: the remaining inventories were never refreshed, no end-of-run summary was logged, and the CronJob exited nonzero. Worse, the offending row kept its old `date_fetched` and never received the `date_refresh_failed` backoff marker — the write that had just failed — so it headed the stalest-first due list on the next run and aborted that batch the same way. A database error while recording a failure is now caught and logged with the inventory's URL, the session is rolled back so the next inventory starts clean, and the batch runs to the end and reports itself. Such failures are counted separately from ordinary refresh failures, and `ook refresh-intersphinx` exits nonzero when any occurred — after printing the run's counts, since a broken bookkeeping path is worth surfacing but is not a reason to abandon the other inventories.

- Stopped the intersphinx cache from storing an empty origin response as a valid inventory. A terminal `200` with a zero-length body — a CDN edge glitch, a truncated object — or a `204`, which is neither a redirect nor a `304`, passed `raise_for_status()` and was written as a `success` row holding zero bytes: a permanent cache hit serving an unparseable inventory with a `200` and the `ETag` of the empty string. Because every later decision point tests whether the stored content is *null* rather than whether it is *empty*, such a row could never cold-miss again, never counted as a live negative-cache entry, and could not be displaced by the negative-cache write's `content IS NULL` guard — a successful background refresh was its only repair, and that is skipped entirely once the row's last request falls outside the active window. An empty terminal body is now an upstream failure on both paths: a negatively cached 502 on the request path, and on the refresh path a skipped inventory that leaves the stored copy serving stale rather than overwriting it with nothing.

- Reported a DNS failure on a requested intersphinx inventory URL as an upstream error rather than as the client's bad request. A resolver blip on a perfectly good `objects.inv` URL was answered with a `400 invalid_inventory_url` carrying the resolver's own message, so a Documenteer client could report a doc author's `intersphinx_mapping` entry as malformed when there was nothing wrong with it — and because a 400 is not an `httpx` error, nothing was cached and every retry re-paid a full `ndots`-expanded lookup in a cluster with no caching resolver. A host that will not resolve, whether the resolver fails or answers with no addresses, is now a negatively cached 502, which is how the identical failure on a redirect hop was already classified. Its detail is generic and the resolver's own text is logged instead of stored, since a negative-cache row's detail is replayed to every client for the whole negative-TTL window. The guard's other refusals are unchanged and still 400: a URL no parser accepts, one that is not `https`, and one whose host resolves to a non-public address are each a fact about the URL the client chose and can fix.

- Rejected an unparseable intersphinx inventory URL with a 400 instead of letting it escape as a 500. The cache's SSRF guard split the requested URL with `urlsplit`, which never validates a port, so `?url=https://docs.python.org:notaport/objects.inv` passed the guard, paid for a DNS lookup, and only then made httpx raise `httpx.InvalidURL` — not an `httpx.HTTPError`, so it escaped every fetch-path handler as an unhandled 500 that was never negatively cached and re-paid the lookup on every repeat. An unterminated IPv6 literal such as `?url=https://[::1/objects.inv` was worse still, raising a bare `ValueError` from inside the guard. The guard now parses the requested URL with `httpx.URL` — the parser the fetch itself connects with, so the host that is validated is the host that is contacted — before resolving anything, and a URL httpx refuses is a 400 naming the parse failure; the requested URL is the client's own choice, so the rejection stays a client error rather than a cached upstream one. A netloc only the stdlib splitter refuses, such as a stray bracket, is percent-encoded by httpx into a host that does not exist, so it is refused a step later by resolution as the negatively cached upstream failure any unresolvable host is. Redirect hops are unaffected: a `Location` that cannot be parsed remains an upstream failure.

- Stopped publishing the SSRF guard's resolution result to clients. That reason names a host and the address it resolves to *from inside the cluster*, and two paths served it verbatim rather than as a transient message to one client. A redirect hop the guard refuses had its reason stored on the negative-cache row and replayed in the 502 body to every client for the whole negative-TTL window, so an origin that redirected an inventory to an internal cluster name published Ook's own DNS view. The refresh job, which re-guards a cached inventory's stored URL before fetching it — the check that catches a once-public host DNS has rebound to a private address — wrote its rejection reason straight to the row's stored error, which the cache replays in the 502 body of every later request that finds the row a live negative-cache entry. Both now store a generic detail (`Upstream redirected the inventory to a disallowed target` for a hop) and log the specific reason as a structured warning instead — for a hop, in one warning record naming both the URL the client requested and the hop that was refused, since the served detail and the negative-cache row now name only the former and the guard's own rejection log names only the latter. A guard rejection of a *client-supplied* URL — one no parser accepts, one that is not `https`, or one pointing at a non-public address — still answers 400 with the specific, actionable reason: that host is the client's own choice and the reason tells them what to fix.

- Stopped following a target no origin ever named when a redirect response repeats its `Location` header. Reading the header concatenated the values with `", "`, which the join then percent-encoded into a single URL naming neither target — one that keeps the origin's host often enough to pass the SSRF guard, be fetched, and 404. In the intersphinx cache that negatively cached a working inventory and 502'd every Documenteer build for the negative-TTL window; in the link checker it reported a working link as broken. Both hop loops now follow the first `Location` value.

- Mirrored the intersphinx cache's redirect hardening in the link checker, whose hop loop is its twin. A checked page — or a redirect target it named — whose host IDNA cannot encode (an empty label, or an all-ASCII one over 63 characters, both of which pass the URL support check and httpx's own parsing) made `getaddrinfo` raise `UnicodeEncodeError`, which none of the checker's handlers caught; a `Location` that overflows httpx's 65,535-character URL limit only once joined raised `httpx.InvalidURL`, which is not an `httpx.HTTPError` and so escaped as well. Either escape propagated through the batch's `asyncio.gather`, discarding every other URL's outcome in the check and rolling the whole run back to pending, where redelivery deterministically re-failed on the same URL. Every resolution failure is now funnelled into a single check failure that stays on the retry ladder, and a malformed redirect target is classified rather than escaping. The hop cap is likewise enforced on the hop count alone, before the target that exceeds it is joined, resolved, or guarded, so an over-long chain always reports the redirect cap rather than the DNS failure — or, worse, the SSRF-guard rejection that parked the URL as `unsupported` with no recheck at all — of a URL the check was never going to request. An identical chain is now classified identically by the link checker and the intersphinx cache.

### Other changes

- The lsst-texmf author ingest now canonicalizes every ORCID it stores, and refuses a run that carries one it cannot. `GET /ook/authors?orcid=` matches with a plain equality so it can ride the `uq_author_orcid` index rather than falling back to a sequential scan, which is correct only while every stored ORCID is the bare, uppercase identifier — an invariant that until now merely happened to hold, since the ingest wrote whatever `authordb.yaml` said, verbatim. Incoming ORCIDs are therefore put through the same normalization the query parameter uses, so a value written as an `orcid.org` URL or with a lowercase checksum character is stored in the form the lookup will find. The pass runs before the duplicate-ORCID pre-check, which compares incoming ORCIDs to stored ones the same way: an un-normalized URL-form value would fail to match its stored bare twin and a real conflict would go unreported. A value that is not an ORCID at all — a bad shape, a non-`orcid.org` host, or a failing ISO 7064 check digit — aborts the ingest before anything is written, with a Slack alert naming every offending author, its rejected value, and the git ref, so one upstream fix round clears the run rather than one per bad entry. Today's `authordb.yaml` passes unchanged: every non-null ORCID in it is already bare, uppercase, shape-valid, and checksummed correctly, so the refusal fires only on a genuinely new bad entry.

- The intersphinx inventory cache now records where a redirecting `objects.inv` URL actually resolved to. Two nullable columns on `intersphinx_inventory` — `resolved_url` and `resolved_redirect_permanent` — store the terminal URL of the redirect chain and whether every hop in it was permanent (a 301 or 308); both are null when the fetch did not redirect at all. They are written on the cold-miss path and on each proactive refresh, including on a `304 Not Modified`, which speaks only to the content and so cannot vouch for a chain that may have moved since the last fetch. A negative-cache row leaves both null, and a fetch failure never clears them from a row that already has content. Any fragment is stripped where a hop target is minted, so a chain ending on a `Location` like `https://docs.example.org/objects.inv#moved` neither records nor serves a fragment-bearing URL for a doc author to paste into `intersphinx_mapping`: a fragment identifies a place inside a document, never part of an inventory's identity as a resource, and is not sent on the wire in any case. Storing the resolution means a permanently-moved inventory URL can be surfaced to doc authors straight from a cache hit, with no second round-trip to the origin. The columns are nullable with no backfill: rows cached before this change populate on their next fetch or refresh.

- A redirecting intersphinx inventory fetch is economical per hop. The fixed 8 KB cap a hop's body is read and discarded under exists so the HTTP/1.1 connection goes back in the pool rather than being closed: reading the body is what lets the motivating `www` → `docs` → `docs` chain reuse a single TCP+TLS connection instead of opening three to the same host on every cold miss and every refresh. A hop answering with more than the drain cap is abandoned, and hop bodies are never counted against `max_content_size`, which governs the terminal inventory response alone. The SSRF guard likewise does not re-resolve a host it already accepted earlier in the same chain — the cluster has no caching resolver and `ndots` search expansion multiplies every external-name lookup — while still resolving each new host, still checking the https-only invariant on every hop, and forgetting the chain's validated hosts when the fetch ends.

- Enabled SQLAlchemy pessimistic connection checking (`pool_pre_ping=True`) on the database session dependency, following the recommendation added in Safir 15.2.0. A connection dropped by the database server while the service was idle is now detected and replaced transparently instead of failing the next request.

- Documented in the intersphinx inventory endpoint's OpenAPI description that the permanent-redirect header must be read per response rather than from an HTTP caching layer: withdrawal of the signal is expressed as the header's absence, and RFC 9111 §4.3.4 lets a `304` update the headers it carries but never delete the ones it omits, so a client caching responses can learn the flag but not unlearn it until the inventory bytes change and force a full `200`. The header's OpenAPI block is also now defined once, keyed by the header-name constant, and shared by the `200` and `304` responses instead of being duplicated in each.

- Documented that `X-Ook-Inventory-Permanent-Redirect` reports the redirect chain observed at the inventory's *last successful fetch*. A row whose background refreshes keep failing retains its resolved-redirect columns — deliberately, since suppressing the header would hide a real permanent move for a full cache lifetime over one transient failure — so the endpoint description now tells clients to read `Age` alongside the header to judge how old the observation is.

<a id='changelog-0.25.1'></a>
## 0.25.1 (2026-08-13)

### Other changes

- Adopted [`faststream_fastapi`](https://faststream-community.github.io/faststream_fastapi/) in place of FastStream's now-deprecated built-in FastAPI plugin (`faststream.kafka.fastapi.KafkaRouter`), which FastStream will remove in 1.0.0. The Kafka subscribers now hang off a plain `KafkaBroker` (`ook.kafkabroker`), and `FastStreamAPI` wraps the FastAPI app in `ook.main` to start and stop the broker around its lifespan. `fastapi.Depends` continues to work inside Kafka handlers, so the `ConsumerContext` injection is unchanged.

- Re-synced the vendored `ruff-shared.toml` with the `lsst/templates` `fastapi_safir_app` copy to ignore `CPY001` (`missing-copyright-notice`), which ruff 0.16 stabilized out of preview and which would otherwise fire on every source file under `select = ["ALL"]`. The redundant `split-on-trailing-comma` setting was dropped from `pyproject.toml`, since `ruff-shared.toml` already sets it.

- Use [prek](https://github.com/j178/prek) in place of `pre-commit` to run the hooks, in the `lint` dependency group, the `Makefile`, the noxfile `lint` session, and CI. `.pre-commit-config.yaml` is unchanged — prek consumes it as-is — so the hook set and its revisions are the same.

<a id='changelog-0.25.0'></a>
## 0.25.0 (2026-07-23)

### New features

- Added a caching proxy for Sphinx intersphinx object inventories. `GET /ook/intersphinx/inventory?url=...` serves a cached `objects.inv` inventory keyed by its full origin URL. On a cache miss the origin is fetched synchronously (SSRF-guarded and bounded by redirect count, timeout, and response size), stored, and served. The response carries the stored content type and an `Age` header giving the seconds since the inventory was fetched upstream. Cache freshness is TTL-aware: an inventory within the TTL is served as a fresh hit, while a stale one is served immediately from the cache and revalidated out of band by the refresh job. A cold-miss upstream failure returns a 502 and is negatively cached so a repeat request inside the negative-TTL window fails fast without re-contacting the origin. The endpoint should be protected via Gafaelfawr ingress configuration. The endpoint returns a strong `ETag` on every `200` response. The value is the quoted SHA-256 hex digest of the served inventory bytes (RFC 9110), so it identifies the bytes Ook currently serves and changes only when the cached inventory content changes. The header is emitted on both the warm cache-hit and cold-miss fetch-then-serve paths, letting clients store it for cheap conditional revalidation. The same endpoint honors the `If-None-Match` request header for cheap revalidation: when a client's validator matches the currently-cached inventory's `ETag`, Ook responds `304 Not Modified` with an empty body and the same `ETag` (and no `Age` header). Comparison follows RFC 9110 weak semantics — the `W/` weakness prefix is ignored, a comma-separated list of validators is accepted, and `If-None-Match: *` matches any cached representation. A stale or non-matching validator still returns the full `200` response with the current `ETag`.
- Added a new `ook refresh-intersphinx` CLI command, intended to run as a scheduled cron job. It conditionally revalidates cached inventories that are past the freshness TTL and were requested by a client within the active window: a `304 Not Modified` keeps the stored content and bumps its fetch time, and a `200` replaces the content and validators. Inventories not requested within the active window are skipped (not deleted) until a new request reactivates them. A `--limit` option caps the number of inventories refreshed per run, and the command reports how many were considered, refreshed, revalidated, and failed.
- New configuration settings for the intersphinx cache: `OOK_INTERSPHINX_TTL` (default 1h) sets the freshness window before a cached inventory is served stale and revalidated, `OOK_INTERSPHINX_NEGATIVE_TTL` (default 5m) sets how long a cold-miss upstream failure is negatively cached, and `OOK_INTERSPHINX_ACTIVE_WINDOW` (default 30d) bounds which recently-requested inventories the `ook refresh-intersphinx` job revalidates.

<a id='changelog-0.24.0'></a>
## 0.24.0 (2026-07-20)

### New features

- The link checker now distinguishes bot-protection blocks and transient server conditions from confirmed breakage. A new `blocked` link status (surfaced in per-URL results and the check summary counts) reports checks that are inconclusive rather than broken: a Cloudflare bot-protection block (an HTTP 403 served by a Cloudflare edge — either carrying a `cf-mitigated` header or a `server: cloudflare` response) and a persistent HTTP 429 rate limit or HTTP 503 outage. These outcomes never escalate the failing-to-broken retry ladder and never reset a link's progress toward broken. A 429's `Retry-After` header is honored (capped) with a single in-run retry, and a 503 is reported as inconclusive immediately.
- Link-check requests now send a browser-prefixed hybrid `User-Agent` by default (Firefox-prefixed, carrying the running Ook version and a contact URL) plus a browser-like `Accept` header, so bot-protection heuristics that reject the bare automation defaults no longer block the checker. The User-Agent is configurable through the new `OOK_LINKCHECK_USER_AGENT` setting.
- New configuration for recheck cadences: `OOK_LINKCHECK_BROKEN_RECHECK_INTERVAL` (default 24h) sets how often a broken link is revisited so a since-fixed link can heal, and `OOK_LINKCHECK_BLOCKED_RECHECK_INTERVAL` (default 1h) sets the near-term cadence for rechecking a blocked link. Rechecks of a persistently blocked link now back off, doubling from the blocked interval with each consecutive blocked outcome and capping at the broken-recheck interval, so a permanently blocked or rate-limited URL converges to a slow cadence instead of rechecking hourly forever.

### Bug fixes

- A HEAD request that returns 429 no longer immediately falls back to a GET request. A rate limit is not the server mishandling HEAD, so the HEAD result is returned and the `Retry-After` logic governs the next request rather than firing a second request straight into the rate limit.

<a id='changelog-0.23.0'></a>
## 0.23.0 (2026-07-09)

### Backwards-incompatible changes

- `POST /ingest/resources/documents` now returns a list of per-item ingest results instead of a bare list of document resources. Each result reports the document's `handle`, a `status` of `created`, `updated`, or `failed`, the stored `resource` (on success), and an `error` detail (on failure). Error details are sanitized to the exception class and first message line, so SQL statements and bound parameters no longer appear in responses.
- Every existing `resource` ID is re-minted as a time-ordered ID in `date_created` order by a one-time Alembic data migration, which rewrites the dependent foreign keys in `document_resource`, `contributor`, and `resource_relation` in the same transaction. This is a deliberate one-time ID break: resource IDs and URLs issued by earlier releases are invalid after the upgrade. This release requires Alembic migrations `cf936213314d`, `3b66bd60b53f`, and `20144e072aa7`.
- Contributor listings in resource responses no longer include the author's `email` address, which is personal data. The affiliation `email_domain` field (a non-personal organizational domain) is unchanged.

### New features

- Added an external link-checking service with a submit-and-poll API. `POST /ook/linkcheck/checks` accepts a website build's external URLs (an `origin_base_url` identifying the website, an `is_default_version` flag, and a list of URLs with the origin-relative page paths they occur on). Any public website is supported: the origin base URL is a full http(s) URL (path-bearing bases like `https://rsp.lsst.io/guides` are allowed) and is normalized by lowercasing the host and stripping any trailing slash. URLs are canonicalized (fragments stripped) and partitioned: URLs with a fresh cached result and unsupported (non-http(s)) URLs resolve immediately, while the rest are checked asynchronously: the submission enqueues an execution request on a new Kafka topic and a FastStream consumer runs the checks. The endpoint returns the created check resource as the body and its URL as the `Location` header: a submission whose URLs all resolve immediately completes at submission and is returned with status 200 (no polling needed), while a submission with URLs to check returns 202 and should be polled at the `Location` header (or the body's `self_url`). `GET /ook/linkcheck/checks/{id}` reports the check's processing status (pending/in_progress/complete), summary counts by URL status, and per-URL results (status, HTTP status code, redirect location). Only default-version submissions replace an origin's recorded URL occurrences; PR-build submissions still receive full results. Link health is tracked per canonical URL across origins with a failing-to-broken retry ladder, so a previously-OK link is only declared broken after repeated failures over a configurable window.
- Added anonymous read endpoints for link health. `GET /ook/linkcheck/urls?url=...` looks up a single canonical URL's stored record: its status, HTTP status code, redirect location, check timestamps, and the origin pages it occurs on (the lookup URL is canonicalized first). `GET /ook/linkcheck/links?origin=...` lists an origin website's links with their health states and page paths, with keyset pagination (`Link` and `X-Total-Count` headers) and a `status` filter: `?status=redirected` lists links whose sources should be updated to their new locations, and `?status=broken` is the rot-monitoring view.
- Added a new `ook linkcheck-recheck` CLI command, intended to run as a daily cron job. It enqueues URLs that are due for a recheck and still occur on at least one origin page as batched `RecheckUrlsMessage` Kafka messages (the Kafka consumer re-checks them and advances their statuses through the retry ladder — this is how a failing link progresses to broken over subsequent days). The command also purges link-check maintenance state: check records older than the retention period and URL records with no remaining page occurrences and no membership in a retained check.
- New configuration settings for link checking: `OOK_LINKCHECK_KAFKA_TOPIC` (default `ook.linkcheck`), `OOK_LINKCHECK_REQUEST_TIMEOUT`, `OOK_LINKCHECK_MAX_CONCURRENCY`, `OOK_LINKCHECK_HOST_INTERVAL`, `OOK_LINKCHECK_FRESHNESS_TTL`, `OOK_LINKCHECK_MAX_URLS_PER_CHECK`, and `OOK_LINKCHECK_CHECK_RETENTION` (default 30d, the age beyond which check records are purged by `ook linkcheck-recheck`). The failing-to-broken retry ladder is tunable through `OOK_LINKCHECK_BROKEN_THRESHOLD` (default 48h), `OOK_LINKCHECK_BROKEN_MIN_ATTEMPTS` (default 3), and `OOK_LINKCHECK_RECHECK_INTERVALS` (default 1h, 4h, 24h, 48h).

### Bug fixes

- Adapt the SDM links domain endpoint to FastAPI 0.137's nested route tree by using `url_path_for` instead of iterating `app.routes`. FastAPI 0.137 nests routes added via `include_router` inside intermediate router objects, so the previous flat iteration over `app.routes` no longer found the SDM link routes and the `/ook/links/domains/sdm` endpoint raised a `KeyError`.

- The service start-up script no longer runs `ook init`. Running `ook init` on every pod start stamped the database at the current Alembic head revision without running migrations, so pending migrations were silently skipped: the pre-deployment `updateSchema` job then saw an up-to-date `alembic_version` and no-oped while the actual tables kept their old shape, and the application's schema-currency check passed against a stamp that was a lie. Schema changes are now applied only by the pre-deployment job (`ook update-db-schema`), and the application refuses to start if the database schema is out of date (the existing `is_database_current` check at startup). The `ook init` command remains available for development bootstrap.

- Link-check execution is now idempotent under Kafka's at-least-once delivery. Re-executing an already-complete check is a no-op, so a completed check is no longer briefly flipped back to `in_progress` (with a stale `date_completed`) while a redelivered execution request re-runs it. A check left `in_progress` by a crashed prior execution is still re-executed to completion on redelivery.

- Hardened the link checker's SSRF guard against DNS rebinding. Link-check HTTP requests now connect to the exact address the SSRF guard validated, pinning the socket to that IP while preserving the original hostname for the `Host` header and for TLS SNI and certificate verification. Previously the guard resolved and validated the host, but httpx independently re-resolved the hostname when connecting, so a low-TTL or rebinding DNS answer could return a public address to the guard and a private one to the connection a moment later. This time-of-check-to-time-of-use gap (applied to the initial URL and to every redirect hop) is now closed.

- Document ingest is now idempotent by natural key. ID minting moved out of the request model and into the storage layer, inside the ingest transaction: an incoming document is resolved against existing rows by matching the database's document-identity unique constraints in turn — `handle` on `document_resource`, then `(series, number)`, then `doi` on `resource` — so a match keeps its ID and takes the update path. Re-ingesting the same payload no longer creates duplicate rows or fails on a unique constraint, and re-ingesting with changed fields (including a changed series or handle) updates the resource in place under the same ID.
- Genuinely new documents now mint a time-ordered ID (via the `ook.domain.base32id` generator) so freshly ingested resources sort in creation order under the default ID-keyset listing. The new row is inserted with `ON CONFLICT (id) DO NOTHING` and retried with a fresh ID on a random collision, so a collision can never silently merge two resources. Ingest timestamps are now stamped in UTC.

- Document ingest now processes each document in its own savepoint and reports its outcome individually. A single malformed document no longer 500s the whole batch or is silently dropped: it is reported as `failed` with an error detail while the remaining documents are still `created` or `updated`. The `created` vs `updated` distinction reuses the natural-key resolution so a document matched to an existing resource reports `updated`.

- External references cited by documents now dedup on any identifier they carry. The DOI-less `ON CONFLICT (url)` upsert path previously raised `ProgrammingError` because no unique index backed the `url` column; a partial unique index on non-null URLs fixes it. References keyed only by an arXiv ID, ISBN, ISSN, or ADS bibcode now upsert on that key instead of violating its unique constraint when re-cited. References with no identifier at all (no DOI, arXiv ID, ISBN, ISSN, bibcode, or URL) are now rejected at ingest and by a database check constraint, so unmergeable duplicates can no longer accumulate.

- Duplicate resource relation edges are now rejected by the database. The previous whole-row unique constraint on `resource_relation` was a no-op under PostgreSQL's default `NULLS DISTINCT` semantics (one of the two related-entity columns is always NULL, making every row distinct); it is replaced by two partial unique indexes, one per edge kind. Supporting indexes were also added for reverse relation lookups (`related_resource_id`, `related_external_ref_id`), relation-type filtering (`(source_resource_id, relation_type)`), and contributor-by-author queries (`contributor.author_id`).

### Other changes

- Ook now runs on Python 3.14. The Docker image is based on `python:3.14.6-slim-bookworm`, the minimum supported Python is now 3.14, and the development environment and CI test against Python 3.14.

- Introduced a Snowflake-style time-ordered ID generator for resource IDs in `ook.domain.base32id` (`generate_resource_id` and `mint_resource_id_for_timestamp`). IDs pack 43 bits of milliseconds since a fixed 2010-01-01 epoch into the high bits plus 17 random low bits, staying within the existing 60-bit / 12-character Crockford Base32 envelope so the API format and serialization are unchanged. The epoch is deliberately non-configurable and predates all Rubin Observatory record-creation dates, so other services can adopt the scheme (and re-mint existing tables) without per-service epoch configuration. Document ingest mints these IDs for new resources, and existing IDs are re-minted by this release's migration, so resource listings under the default ID-keyset ordering follow creation order.

<a id='changelog-0.22.0'></a>
## 0.22.0 (2026-06-10)

### New features

- Added author internal ID aliases so that two authordb.yaml IDs that correspond to the same person (and therefore the same ORCID) can coexist. An alias resolves to its root author: `GET /ook/authors/{internal_id}` with an alias returns the root author's record, document ingests attribute contributors referencing an alias to the root author, and the lsst-texmf ingest skips authordb.yaml entries whose keys are registered aliases instead of failing on a duplicate ORCID. Aliases are managed through new admin endpoints: `GET /ook/admin/authors/aliases`, `POST /ook/admin/authors/aliases` (with an `alias`/`canonical` request body), and `DELETE /ook/admin/authors/aliases/{alias}`. Creating an alias for an internal ID that already exists as an author record merges that record into the root author, re-pointing existing document attributions.

### Bug fixes

- `Factory.create_standalone` now stops the Kafka broker when its context exits. Previously the broker (a module-level singleton shared with the FastAPI app's Kafka router) was left connected, leaking aiokafka producers bound to the event loop that created them. In the test suite this caused an "Event loop is closed" error during app shutdown whenever a test using the standalone `factory` fixture ran before a handler test in the same pytest invocation.

<a id='changelog-0.21.0'></a>
## 0.21.0 (2025-10-28)

### New features

- Added Slack webhook notifications for lsst-texmf author ingest issues. When the `OOK_SLACK_WEBHOOK` environment variable is configured, Ook now sends notifications for:

  - **Stale author entries**: When author IDs are renamed in lsst-texmf's authordb.yaml, the old entries that remain in Ook's database are detected and reported (without automatic deletion).
  - **Duplicate ORCID violations**: When an author ID changes but keeps the same ORCID, causing a unique constraint violation, a detailed notification is sent with information about both the existing and new author entries, including likely causes and resolution steps.

- Added admin API endpoint `DELETE /ook/admin/authors/{internal_id}` for manually deleting author entries when resolving stale entries or ORCID conflicts.
  These admin endpoints are intended to be protected with Gafaelfawr scope such as `exec:internal-tools`.

- Ook's docker images are now built for both amd64 and arm64 architectures.

### Bug fixes

- The UTF-8 BOM is now properly handled when reading CSV files for glossary ingest, preventing parsing errors.

### Other changes

- Improved testcontainers setup for compatibility with Colima on macOS.

<a id='changelog-0.20.0'></a>
## 0.20.0 (2025-08-07)

### Backwards-incompatible changes

- This version adds a new `address_country_code` column to the `affiliation` table. This requires an Alembic migration, `c03d146610d8` to `8e529b9177a0`.

### New features

- Ook now stores the two-letter ISO 3166-1 country code for an affiliation in the `affiliation` table, in addition to storing the country as provided by `authordb.yaml`. When a country code is available, it is used for determining the affiliation's country name, with a fallback to the country name column when absent. This should add reliability to affiliation address data.

- The new CLI command, `ook migrate-country-codes` migrates existing country names to the country codes column.

- Added formatted address field to affiliation responses. Affiliation addresses now include a `formatted` field that
contains properly formatted address strings using international standards.
  - Uses the [google-i18n-address](https://github.com/mirumee/google-i18n-address) library to respect country-specific conventions for address layout
  - Includes graceful fallback formatting for invalid or incomplete address data
  - Maintains full backwards compatibility with existing API consumers

<a id='changelog-0.19.0'></a>

## 0.19.0 (2025-08-04)

### Backwards-incompatible changes

- A database migration is required to add a new `search_vector` column to the `author` table. This column enables full-text search capabilities and is populated with computed values from the `given_name` and `surname` fields. Requires Alembic migration from `1ad667eab84e` to `c03d146610d8`.

### New features

- The `/authors` endpoint now supports a `search` query parameter that allows for flexible and typo-tolerant searching of authors by name. The search system automatically detects and handles various name formats:

  - "Last, First"
  - "Last, Initial"
  - "First Last"
  - Family name only
  - Given name only
  - Compound family names
  - Names with suffixes
  - Partial names, initials, and typos

### Other changes

- Improved codebase compatibility with coding agents like Claude:

  - Streamlined the logging output from `nox` tests to reduce noise.
  - Added Claude context file (`CLAUDE.md`) with project instructions.

<a id='changelog-0.18.0'></a>

## 0.18.0 (2025-07-29)

### Backwards-incompatible changes

- This release requires a database migration to add new tables for the resources API: `113ced7d2d29` to `1ad667eab84e`.

### New features

- Ook now has a bibliographic resource API for storing metadata records about Rubin Observatory documentation (technical notes, documents, user guides), software code bases, and other resources:

  - Core data model designed to be compatible with DataCite concepts for straightforward integration with DataCite DOI registration.

  - Polymorphic resource model allows different types of resources (documents, software, datasets) to be stored efficiently. This release demonstrates this model with a `Document` resource type.

  - Support for relationships between records and external references (such as papers with DOIs). Relationships are annotated with DataCite relationship types to enable features such as reference tracking and tracing documents that supersede other documents.

  - Integration with the existing author API for both author lists and tracking other types of contributors.

  - Resources are available through `GET /resources` and `GET /resources/{id}` endpoints. These endpoints should be considered experimental and subject to change in future releases.

  This bibliographic API will enable features such as sophisticated documentation search APIs and user interfaces, automation for DOI registration, and more. Future releases will integrate Ook's existing documentation ingest processes with the bibliographic database and develop API endpoints for querying and managing bibliographic resources.

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
