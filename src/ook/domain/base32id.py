"""Pydantic helpers for Crockford Base32 identifiers with checksum support.

This module provides utilities to create Pydantic fields that validate and
serialize Crockford Base32 identifiers using the base32-lib package. The
identifiers are stored internally as integers but validated from and
serialized to base32 strings with checksums.

Example
-------
>>> from pydantic import BaseModel
>>> from ook.domain.base32id import Base32Id
>>>
>>> class MyModel(BaseModel):
...     id: Base32Id
>>>
>>> # Validate from string
>>> model = MyModel.model_validate({"id": "1234-5678-90ab-cd2f"})
>>> assert isinstance(model.id, int)
>>>
>>> # Serialize to JSON with base32 string
>>> json_data = model.model_dump()
>>> assert isinstance(json_data["id"], str)

References
----------
- base32-lib: https://base32-lib.readthedocs.io/en/latest/
- Bibliography API design: planning/bibliography-api.md
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import base32_lib
from pydantic import PlainSerializer, PlainValidator

__all__ = [
    "BASE32_ID_LENGTH",
    "BASE32_ID_SPLIT_EVERY",
    "RESOURCE_ID_EPOCH",
    "RESOURCE_ID_RANDOM_BITS",
    "RESOURCE_ID_TIMESTAMP_BITS",
    "Base32Id",
    "Base32IdNoHyphens",
    "Base32IdShort",
    "generate_base32_id",
    "generate_resource_id",
    "mint_resource_id_for_timestamp",
    "mint_time_ordered_resource_ids",
    "serialize_base32_id",
    "serialize_ook_base32_id",
    "validate_base32_id",
]

# Constants for Base32Id formatting to ensure consistency
BASE32_ID_LENGTH = 12
"""Default length for Base32Id before checksum."""

BASE32_ID_SPLIT_EVERY = 4
"""Default number of characters between hyphens for Base32Id."""

RESOURCE_ID_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)
"""Custom epoch (2025-01-01T00:00:00Z) for time-ordered resource IDs."""

RESOURCE_ID_TIMESTAMP_BITS = 43
"""High-order bits encoding milliseconds since `RESOURCE_ID_EPOCH`.

43 bits of milliseconds gives roughly 278 years of runway from the epoch.
"""

RESOURCE_ID_RANDOM_BITS = 17
"""Low-order random bits (~131k distinct values per millisecond).

`RESOURCE_ID_TIMESTAMP_BITS` + `RESOURCE_ID_RANDOM_BITS` == 60, which is the
Crockford Base32 envelope (12 characters at 5 bits each) that `Base32Id`
serializes.
"""


def validate_base32_id(value: Any) -> int:
    """
    Validate a base32 identifier and return the integer value.

    Parameters
    ----------
    value
        The identifier value as an integer or base32 string with checksum.

    Returns
    -------
    int
        The decoded integer value.

    Raises
    ------
    ValueError
        If the value is not a valid integer or base32 string with checksum.
    """
    if isinstance(value, int):
        if value < 0:
            raise ValueError("Base32 ID must be non-negative integer")
        return value
    elif isinstance(value, str):
        try:
            # Remove hyphens and decode with checksum validation
            clean_value = value.replace("-", "")
            return base32_lib.decode(clean_value, checksum=True)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid base32 identifier: {e}") from e
    else:
        raise ValueError(  # noqa: TRY004
            f"Base32 ID must be int or str, got {type(value).__name__}"
        )


def serialize_base32_id(
    value: int, *, split_every: int = 4, length: int = 12
) -> str:
    """Serialize an integer to a base32 string with checksum and hyphens.

    Parameters
    ----------
    value
        The integer value to encode.
    split_every
        Number of characters between hyphens (default: 4).
    length
        Minimum length of the base32 string before checksum (default: 12).

    Returns
    -------
    str
        Base32 string with checksum and hyphens.
    """
    # Encode with checksum but no automatic splitting
    encoded = base32_lib.encode(
        value, min_length=length, checksum=True, split_every=0
    )

    # Add manual splitting if requested
    if split_every > 0:
        parts = [
            encoded[i : i + split_every]
            for i in range(0, len(encoded), split_every)
        ]
        return "-".join(parts)

    return encoded


def serialize_ook_base32_id(
    value: int,
    *,
    split_every: int = BASE32_ID_SPLIT_EVERY,
    length: int = BASE32_ID_LENGTH,
) -> str:
    """Serialize a `Base32Id` to a string following Ook's conventions."""
    return serialize_base32_id(value, split_every=split_every, length=length)


type Base32Id = Annotated[
    int,
    PlainValidator(validate_base32_id),
    PlainSerializer(
        serialize_ook_base32_id,
        return_type=str,
        when_used="json",
    ),
]
"""Base32 identifier with checksum and hyphens (length of 12+2)."""

type Base32IdNoHyphens = Annotated[
    int,
    PlainValidator(validate_base32_id),
    PlainSerializer(
        lambda value: serialize_base32_id(value, split_every=0, length=12),
        return_type=str,
        when_used="json",
    ),
]
"""Base32 identifier with checksum, no hyphens (length of 12+2)."""

type Base32IdShort = Annotated[
    int,
    PlainValidator(validate_base32_id),
    PlainSerializer(
        lambda value: serialize_base32_id(value, split_every=4, length=8),
        return_type=str,
        when_used="json",
    ),
]
"""Base32 identifier with checksum and hyphens (length of 8+2)."""


def generate_base32_id(*, length: int = 12, split_every: int = 4) -> str:
    """Generate a new random base32 identifier with checksum.

    Parameters
    ----------
    length
        Length of the identifier before checksum (default: 12).
    split_every
        Number of characters between hyphens (default: 4).
        Set to 0 to disable hyphenation.

    Returns
    -------
    str
        A new base32 identifier string with checksum and hyphens.

    Example
    -------
    >>> new_id = generate_base32_id()
    >>> len(new_id.replace("-", ""))  # Should be length + 2 for checksum
    14
    """
    return base32_lib.generate(
        length=length + 2, split_every=split_every, checksum=True
    )


def mint_resource_id_for_timestamp(timestamp: datetime) -> int:
    """Mint a time-ordered resource ID for a specific timestamp.

    The ID is a Snowflake-style integer inside the 60-bit Crockford Base32
    envelope: the high `RESOURCE_ID_TIMESTAMP_BITS` bits hold milliseconds
    since `RESOURCE_ID_EPOCH` and the low `RESOURCE_ID_RANDOM_BITS` bits are
    random. IDs minted for later timestamps therefore sort after earlier ones
    under plain integer (and thus Base32 keyset) ordering. This is
    deliberately *not* a truncated UUIDv7 (a 60-bit envelope cannot hold one,
    and its 1970-anchored 48-bit timestamp would leave only 12 random bits).

    Parameters
    ----------
    timestamp
        A timezone-aware timestamp at or after `RESOURCE_ID_EPOCH`. The
        re-mint migration reuses this helper to derive IDs from a row's
        ``date_created``.

    Returns
    -------
    int
        An integer that fits the 60-bit envelope, suitable for the
        ``resource.id`` column and round-trippable through
        `serialize_ook_base32_id` / `validate_base32_id`.

    Raises
    ------
    ValueError
        If the timestamp is timezone-naive, precedes `RESOURCE_ID_EPOCH`, or
        is far enough in the future to overflow the timestamp bits.
    """
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")

    milliseconds = (timestamp - RESOURCE_ID_EPOCH) // timedelta(milliseconds=1)
    if milliseconds < 0:
        raise ValueError(
            "timestamp must not precede the resource ID epoch "
            f"({RESOURCE_ID_EPOCH.isoformat()})"
        )
    if milliseconds >= (1 << RESOURCE_ID_TIMESTAMP_BITS):
        raise ValueError(
            "timestamp exceeds the 60-bit resource ID timestamp envelope"
        )

    random_bits = secrets.randbits(RESOURCE_ID_RANDOM_BITS)
    return (milliseconds << RESOURCE_ID_RANDOM_BITS) | random_bits


def mint_time_ordered_resource_ids(
    timestamps: Sequence[datetime],
) -> list[int]:
    """Mint strictly increasing, time-ordered resource IDs for a sequence of
    timestamps supplied in ascending order.

    Each ID is derived from its timestamp with
    `mint_resource_id_for_timestamp`, so the sequence tracks wall-clock order.
    When consecutive timestamps fall in the same millisecond (or the random low
    bits would otherwise regress), the ID is bumped to ``previous + 1`` so the
    returned sequence is *strictly* increasing regardless of tie density. This
    is the generator the one-time re-mint migration uses to reassign every
    ``resource.id`` in ``date_created`` order.

    Parameters
    ----------
    timestamps
        Timezone-aware timestamps in ascending order (typically each row's
        ``date_created``). Ties are resolved by the caller's ordering.

    Returns
    -------
    list[int]
        One strictly increasing integer ID per input timestamp, each within the
        60-bit Crockford Base32 envelope.

    Raises
    ------
    ValueError
        If any timestamp is rejected by `mint_resource_id_for_timestamp`.
    """
    ids: list[int] = []
    previous: int | None = None
    for timestamp in timestamps:
        candidate = mint_resource_id_for_timestamp(timestamp)
        if previous is not None and candidate <= previous:
            candidate = previous + 1
        ids.append(candidate)
        previous = candidate
    return ids


def generate_resource_id() -> int:
    """Mint a time-ordered resource ID for the current time.

    Returns
    -------
    int
        A time-ordered integer for the ``resource.id`` column. See
        `mint_resource_id_for_timestamp` for the bit layout.
    """
    return mint_resource_id_for_timestamp(datetime.now(tz=UTC))
