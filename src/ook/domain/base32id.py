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

from typing import Annotated, Any

import base32_lib
from pydantic import PlainSerializer, PlainValidator

__all__ = [
    "BASE32_ID_LENGTH",
    "BASE32_ID_SPLIT_EVERY",
    "Base32Id",
    "Base32IdNoHyphens",
    "Base32IdShort",
    "generate_base32_id",
    "serialize_base32_id",
    "serialize_ook_base32_id",
    "validate_base32_id",
]

# Constants for Base32Id formatting to ensure consistency
BASE32_ID_LENGTH = 12
"""Default length for Base32Id before checksum."""

BASE32_ID_SPLIT_EVERY = 4
"""Default number of characters between hyphens for Base32Id."""


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
        lambda value: serialize_ook_base32_id(value),
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
