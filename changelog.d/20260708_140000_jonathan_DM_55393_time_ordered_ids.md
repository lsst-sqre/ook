### Other changes

- Introduced a Snowflake-style time-ordered ID generator for resource IDs in `ook.domain.base32id` (`generate_resource_id` and `mint_resource_id_for_timestamp`). IDs pack 43 bits of milliseconds since a fixed 2010-01-01 epoch into the high bits plus 17 random low bits, staying within the existing 60-bit / 12-character Crockford Base32 envelope so the API format and serialization are unchanged. This is groundwork toward creation-ordered resource listings; ingest adoption and the re-mint migration follow in later changes.
