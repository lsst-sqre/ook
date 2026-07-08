### Bug fixes

- Link-check execution is now idempotent under Kafka's at-least-once delivery. Re-executing an already-complete check is a no-op, so a completed check is no longer briefly flipped back to `in_progress` (with a stale `date_completed`) while a redelivered execution request re-runs it. A check left `in_progress` by a crashed prior execution is still re-executed to completion on redelivery.
