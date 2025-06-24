"""Tests for the base32id module."""

from __future__ import annotations

import json
from typing import Annotated

import base32_lib
import pytest
from pydantic import BaseModel, Field, ValidationError

from ook.domain.base32id import (
    Base32Id,
    Base32IdNoHyphens,
    Base32IdShort,
    generate_base32_id,
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
