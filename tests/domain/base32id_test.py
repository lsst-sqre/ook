"""Tests for the base32id module."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Annotated

import base32_lib
import pytest
from pydantic import BaseModel, Field, ValidationError

from ook.domain.base32id import (
    RESOURCE_ID_EPOCH,
    RESOURCE_ID_RANDOM_BITS,
    RESOURCE_ID_TIMESTAMP_BITS,
    Base32Id,
    Base32IdNoHyphens,
    Base32IdShort,
    generate_base32_id,
    generate_resource_id,
    mint_resource_id_for_timestamp,
    mint_time_ordered_resource_ids,
    serialize_ook_base32_id,
    validate_base32_id,
)


def test_validate_from_integer() -> None:
    """Test validation from integer input."""

    class Model(BaseModel):
        id: Base32Id

    # Valid integer
    model = Model.model_validate({"id": 12345})
    assert model.id == 12345
    assert isinstance(model.id, int)


def test_validate_from_string() -> None:
    """Test validation from base32 string input."""

    class Model(BaseModel):
        id: Annotated[
            Base32Id,
            Field(
                description="Base32 ID with checksum",
            ),
        ]

    # Generate a valid base32 string
    test_id = generate_base32_id()
    # Decode it to get the integer value
    expected_int = base32_lib.decode(test_id.replace("-", ""), checksum=True)

    model = Model.model_validate({"id": test_id})
    assert model.id == expected_int
    assert isinstance(model.id, int)


def test_validate_from_string_no_hyphens() -> None:
    """Test validation from base32 string without hyphens."""

    class Model(BaseModel):
        id: Base32Id

    # Generate and remove hyphens
    test_id = generate_base32_id().replace("-", "")
    expected_int = base32_lib.decode(test_id, checksum=True)

    model = Model.model_validate({"id": test_id})
    assert model.id == expected_int
    assert isinstance(model.id, int)


def test_validate_negative_integer_fails() -> None:
    """Test that negative integers are rejected."""

    class Model(BaseModel):
        id: Base32Id

    with pytest.raises(
        ValidationError, match="Base32 ID must be non-negative"
    ):
        Model.model_validate({"id": -1})


def test_validate_invalid_string_fails() -> None:
    """Test that invalid base32 strings are rejected."""

    class Model(BaseModel):
        id: Base32Id

    # Invalid characters
    with pytest.raises(ValidationError, match="Invalid base32"):
        Model.model_validate({"id": "invalid!"})

    # Invalid checksum
    with pytest.raises(ValidationError, match="Invalid base32"):
        Model.model_validate({"id": "1234-5678-90ab-cdxx"})


def test_validate_wrong_type_fails() -> None:
    """Test that non-int/non-str types are rejected."""

    class Model(BaseModel):
        id: Base32Id

    with pytest.raises(ValidationError, match="must be int or str"):
        Model.model_validate({"id": 12.34})

    with pytest.raises(ValidationError, match="must be int or str"):
        Model.model_validate({"id": ["list"]})


def test_serialize_to_json() -> None:
    """Test serialization to JSON with base32 string."""

    class Model(BaseModel):
        id: Base32Id

    model = Model.model_validate({"id": 12345})
    json_str = model.model_dump_json()
    json_data = json.loads(json_str)

    # Should be a string in JSON
    assert isinstance(json_data["id"], str)
    # Should have hyphens
    assert "-" in json_data["id"]
    # Should be decodable back to original integer
    decoded = base32_lib.decode(
        json_data["id"].replace("-", ""), checksum=True
    )
    assert decoded == 12345


def test_serialize_to_dict() -> None:
    """Test serialization to dict maintains integer."""

    class Model(BaseModel):
        id: Base32Id

    model = Model.model_validate({"id": 12345})
    data = model.model_dump()

    # Should still be an integer in dict mode
    assert isinstance(data["id"], int)
    assert data["id"] == 12345


def test_serialize_no_hyphens() -> None:
    """Test serialization without hyphens."""

    class Model(BaseModel):
        id: Base32IdNoHyphens

    model = Model.model_validate({"id": 12345})
    json_str = model.model_dump_json()
    json_data = json.loads(json_str)

    # Should not have hyphens
    assert "-" not in json_data["id"]


def test_serialize_with_custom_length() -> None:
    """Test serialization with custom minimum length."""

    class Model(BaseModel):
        id: Base32IdShort

    model = Model.model_validate({"id": 1})  # Small number
    json_str = model.model_dump_json()
    json_data = json.loads(json_str)

    # Remove hyphens to check total length (including checksum)
    clean_id = json_data["id"].replace("-", "")
    # Base32IdShort has length=8, which means 8 total chars with checksum
    assert len(clean_id) == 8


def test_generate_default() -> None:
    """Test generation with default parameters."""
    new_id = generate_base32_id()

    # Should have hyphens
    assert "-" in new_id
    # Should be decodable
    decoded = base32_lib.decode(new_id.replace("-", ""), checksum=True)
    assert isinstance(decoded, int)
    assert decoded >= 0

    # Should be 12 + 2 = 14 characters without hyphens
    clean_id = new_id.replace("-", "")
    assert len(clean_id) == 14


def test_generate_custom_length() -> None:
    """Test generation with custom length."""
    new_id = generate_base32_id(length=8)

    # Should be 8 + 2 = 10 characters without hyphens
    clean_id = new_id.replace("-", "")
    assert len(clean_id) == 10


def test_generate_no_hyphens() -> None:
    """Test generation without hyphens."""
    new_id = generate_base32_id(split_every=0)

    # Should not have hyphens
    assert "-" not in new_id


def test_generate_custom_split() -> None:
    """Test generation with custom hyphen spacing."""
    new_id = generate_base32_id(split_every=5)

    # Should have hyphens every 5 characters
    id_parts = new_id.split("-")
    for part in id_parts[:-1]:
        assert len(part) == 5


def test_resource_id_epoch_is_2025_utc() -> None:
    """The custom epoch is 2025-01-01T00:00:00Z and timezone-aware."""
    assert datetime(2025, 1, 1, tzinfo=UTC) == RESOURCE_ID_EPOCH
    assert RESOURCE_ID_EPOCH.tzinfo is not None


def test_resource_id_bit_layout_fills_60bit_envelope() -> None:
    """Timestamp and random bits together fill the 60-bit envelope."""
    assert RESOURCE_ID_TIMESTAMP_BITS == 43
    assert RESOURCE_ID_RANDOM_BITS == 17
    assert RESOURCE_ID_TIMESTAMP_BITS + RESOURCE_ID_RANDOM_BITS == 60


def test_mint_at_epoch_has_zero_timestamp_bits() -> None:
    """An ID minted at the epoch has no timestamp component."""
    resource_id = mint_resource_id_for_timestamp(RESOURCE_ID_EPOCH)

    # High (timestamp) bits are zero, so only the random low bits remain.
    assert resource_id >> RESOURCE_ID_RANDOM_BITS == 0
    assert 0 <= resource_id < (1 << RESOURCE_ID_RANDOM_BITS)


def test_mint_timestamp_bits_derive_from_epoch() -> None:
    """The high bits encode milliseconds since the custom epoch."""
    for offset_ms in (1, 1000, 123_456, 5_000_000_000):
        timestamp = RESOURCE_ID_EPOCH + timedelta(milliseconds=offset_ms)
        resource_id = mint_resource_id_for_timestamp(timestamp)
        assert resource_id >> RESOURCE_ID_RANDOM_BITS == offset_ms


def test_mint_is_monotonic_across_timestamps() -> None:
    """Later timestamps sort after earlier ones under integer ordering."""
    timestamps = [
        RESOURCE_ID_EPOCH + timedelta(milliseconds=ms)
        for ms in (0, 1, 2, 1000, 86_400_000, 10_000_000_000)
    ]
    ids = [mint_resource_id_for_timestamp(ts) for ts in timestamps]

    # Any timestamps at least 1 ms apart must be strictly ordered despite the
    # random low bits (the 1 ms step dominates the 17-bit random range).
    assert ids == sorted(ids)
    assert all(earlier < later for earlier, later in pairwise(ids))


def test_mint_fits_60bit_envelope() -> None:
    """Every minted ID fits within the 60-bit / 12-character envelope."""
    for offset_ms in (0, 1, 1_000_000, (1 << RESOURCE_ID_TIMESTAMP_BITS) - 1):
        timestamp = RESOURCE_ID_EPOCH + timedelta(milliseconds=offset_ms)
        resource_id = mint_resource_id_for_timestamp(timestamp)
        assert 0 <= resource_id < (1 << 60)


def test_mint_round_trips_through_serialize_and_validate() -> None:
    """A minted ID round-trips through the Base32 API format."""
    timestamp = RESOURCE_ID_EPOCH + timedelta(milliseconds=1_234_567_890)
    resource_id = mint_resource_id_for_timestamp(timestamp)

    encoded = serialize_ook_base32_id(resource_id)
    assert validate_base32_id(encoded) == resource_id
    # Fits the 60-bit / 12-character envelope: at most 12 data characters
    # plus the 2-character checksum once hyphens are removed.
    assert len(encoded.replace("-", "")) <= 14


def test_max_envelope_value_encodes_to_twelve_chars_plus_checksum() -> None:
    """The largest 60-bit value uses all 12 data characters + checksum."""
    max_value = (1 << 60) - 1
    encoded = serialize_ook_base32_id(max_value)

    # 12 data characters + 2 checksum characters once hyphens are removed.
    assert len(encoded.replace("-", "")) == 14
    assert validate_base32_id(encoded) == max_value


def test_mint_same_millisecond_shares_timestamp_bits() -> None:
    """Same-millisecond IDs share timestamp bits but vary in random bits."""
    timestamp = RESOURCE_ID_EPOCH + timedelta(milliseconds=42)
    ids = [mint_resource_id_for_timestamp(timestamp) for _ in range(50)]

    # All share the same timestamp (high) bits.
    high_bits = {resource_id >> RESOURCE_ID_RANDOM_BITS for resource_id in ids}
    assert high_bits == {42}

    # The random low bits vary across mints in the same millisecond.
    assert len(set(ids)) > 1


def test_mint_before_epoch_raises() -> None:
    """A timestamp before the epoch is rejected."""
    before = RESOURCE_ID_EPOCH - timedelta(milliseconds=1)
    with pytest.raises(ValueError, match="epoch"):
        mint_resource_id_for_timestamp(before)


def test_mint_naive_timestamp_raises() -> None:
    """A timezone-naive timestamp is rejected."""
    naive = datetime(2025, 6, 1)  # noqa: DTZ001
    with pytest.raises(ValueError, match="timezone-aware"):
        mint_resource_id_for_timestamp(naive)


def test_mint_beyond_envelope_raises() -> None:
    """A timestamp past the 43-bit timestamp envelope is rejected."""
    beyond = RESOURCE_ID_EPOCH + timedelta(
        milliseconds=1 << RESOURCE_ID_TIMESTAMP_BITS
    )
    with pytest.raises(ValueError, match="envelope"):
        mint_resource_id_for_timestamp(beyond)


def test_generate_resource_id_reflects_now() -> None:
    """generate_resource_id mints for the current time within the envelope."""
    before_ms = (datetime.now(tz=UTC) - RESOURCE_ID_EPOCH) // timedelta(
        milliseconds=1
    )
    resource_id = generate_resource_id()
    after_ms = (datetime.now(tz=UTC) - RESOURCE_ID_EPOCH) // timedelta(
        milliseconds=1
    )

    assert 0 <= resource_id < (1 << 60)
    assert before_ms <= (resource_id >> RESOURCE_ID_RANDOM_BITS) <= after_ms


def test_mint_time_ordered_empty() -> None:
    """An empty sequence of timestamps yields no IDs."""
    assert mint_time_ordered_resource_ids([]) == []


def test_mint_time_ordered_preserves_count() -> None:
    """One ID is minted per input timestamp."""
    timestamps = [
        RESOURCE_ID_EPOCH + timedelta(milliseconds=ms)
        for ms in (0, 5, 5, 5, 1000, 2000)
    ]
    ids = mint_time_ordered_resource_ids(timestamps)
    assert len(ids) == len(timestamps)


def test_mint_time_ordered_is_strictly_increasing() -> None:
    """Distinct ascending timestamps mint strictly increasing IDs."""
    timestamps = [
        RESOURCE_ID_EPOCH + timedelta(milliseconds=ms)
        for ms in (0, 1, 2, 1000, 86_400_000, 10_000_000_000)
    ]
    ids = mint_time_ordered_resource_ids(timestamps)

    assert ids == sorted(ids)
    assert all(earlier < later for earlier, later in pairwise(ids))


def test_mint_time_ordered_breaks_millisecond_ties_strictly() -> None:
    """IDs stay strictly increasing when timestamps share a millisecond."""
    # Every timestamp is identical, so the timestamp bits collide and only the
    # tie-breaking bump keeps the sequence strictly increasing.
    timestamp = RESOURCE_ID_EPOCH + timedelta(milliseconds=42)
    ids = mint_time_ordered_resource_ids([timestamp] * 200)

    assert len(ids) == 200
    assert len(set(ids)) == 200
    assert all(earlier < later for earlier, later in pairwise(ids))


def test_mint_time_ordered_anchors_to_first_timestamp() -> None:
    """The first minted ID encodes the first timestamp's millisecond bits."""
    timestamps = [
        RESOURCE_ID_EPOCH + timedelta(milliseconds=ms) for ms in (7, 7, 99)
    ]
    ids = mint_time_ordered_resource_ids(timestamps)

    assert ids[0] >> RESOURCE_ID_RANDOM_BITS == 7


def test_mint_time_ordered_fits_envelope() -> None:
    """Every minted ID fits within the 60-bit Base32 envelope."""
    timestamps = [
        RESOURCE_ID_EPOCH + timedelta(milliseconds=ms)
        for ms in (0, 0, 0, 500, 500, 1_000_000)
    ]
    ids = mint_time_ordered_resource_ids(timestamps)

    assert all(0 <= resource_id < (1 << 60) for resource_id in ids)


def test_user_example() -> None:
    """Test the exact functionality requested by the user."""

    class MyModel(BaseModel):
        id: Base32Id

    # Generate a test ID similar to user's example
    id_string = base32_lib.generate(length=14, split_every=4, checksum=True)

    # Validate from string
    m = MyModel.model_validate({"id": id_string})
    assert isinstance(m.id, int)

    # Serialize to JSON
    json_str = m.model_dump_json()
    json_data = json.loads(json_str)
    assert isinstance(json_data["id"], str)

    # Verify round-trip
    original_int = base32_lib.decode(id_string.replace("-", ""), checksum=True)
    assert m.id == original_int

    # Verify serialized string is valid
    decoded_from_json = base32_lib.decode(
        json_data["id"].replace("-", ""), checksum=True
    )
    assert decoded_from_json == original_int
