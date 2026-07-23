### New features

- The intersphinx inventory cache endpoint (`GET /ook/intersphinx/inventory?url=...`) now returns a strong `ETag` on every `200` response. The value is the quoted SHA-256 hex digest of the served inventory bytes (RFC 9110), so it identifies the bytes Ook currently serves and changes only when the cached inventory content changes. The header is emitted on both the warm cache-hit and cold-miss fetch-then-serve paths, letting clients store it for cheap conditional revalidation.
