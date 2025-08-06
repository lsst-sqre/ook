"""Tests for AuthorStore migrate_country_codes method."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from ook.factory import Factory


@pytest.mark.asyncio
async def test_migrate_country_codes_empty_database(
    factory: Factory,
) -> None:
    """Test migrate_country_codes with no records to process."""
    async with factory.db_session.begin():
        author_store = factory.create_author_store()
        result = await author_store.migrate_country_codes()

        assert result["updated"] == 0
        assert result["failed"] == 0
        assert result["total"] == 0
        assert result["failed_conversions"] == []
        assert result["conversions"] == {}


@pytest.mark.asyncio
async def test_migrate_country_codes_dry_run(
    factory: Factory,
) -> None:
    """Test migrate_country_codes in dry run mode."""
    async with factory.db_session.begin():
        author_store = factory.create_author_store()
        # Insert test data
        await author_store._session.execute(
            text("""
                INSERT INTO affiliation (
                    internal_id, name, address_country, date_updated
                )
                VALUES
                    ('test1', 'Test Org 1', 'United States', NOW()),
                    ('test2', 'Test Org 2', 'Canada', NOW()),
                    ('test3', 'Test Org 3', 'Invalid Country Name', NOW())
            """)
        )

        result = await author_store.migrate_country_codes(dry_run=True)

        assert result["updated"] == 2
        assert result["failed"] == 1
        assert result["total"] == 3
        assert len(result["failed_conversions"]) == 1
        assert len(result["conversions"]) == 2

        # Verify conversions contain expected mappings
        conversions = result["conversions"]
        country_codes = [conv["country_code"] for conv in conversions.values()]
        assert "US" in country_codes
        assert "CA" in country_codes

        # Verify failed conversion details
        failed_names = [
            fc["country_name"] for fc in result["failed_conversions"]
        ]
        assert "Invalid Country Name" in failed_names


@pytest.mark.asyncio
async def test_migrate_country_codes_actual_update(
    factory: Factory,
) -> None:
    """Test migrate_country_codes with actual database updates."""
    async with factory.db_session.begin():
        author_store = factory.create_author_store()
        # Insert test data
        await author_store._session.execute(
            text("""
                INSERT INTO affiliation (
                    internal_id, name, address_country, date_updated
                )
                VALUES
                    ('test1', 'Test Org 1', 'United States', NOW()),
                    ('test2', 'Test Org 2', 'UK', NOW())
            """)
        )

        result = await author_store.migrate_country_codes(dry_run=False)

        assert result["updated"] == 2
        assert result["failed"] == 0
        assert result["total"] == 2
        assert result["failed_conversions"] == []
        assert result["conversions"] == {}  # Not included in non-dry run

        # Verify database was actually updated
        check_result = await author_store._session.execute(
            text("""
                SELECT internal_id, address_country_code
                FROM affiliation
                WHERE internal_id IN ('test1', 'test2')
                ORDER BY internal_id
            """)
        )

        records = check_result.fetchall()
        assert len(records) == 2
        assert records[0][1] == "US"  # United States -> US
        assert records[1][1] == "GB"  # UK -> GB


@pytest.mark.asyncio
async def test_migrate_country_codes_skips_existing_codes(
    factory: Factory,
) -> None:
    """Test migrate_country_codes skips records with existing codes."""
    async with factory.db_session.begin():
        author_store = factory.create_author_store()
        # Insert test data with existing country codes
        await author_store._session.execute(
            text("""
                INSERT INTO affiliation (
                    internal_id, name, address_country,
                    address_country_code, date_updated
                )
                VALUES
                    ('test1', 'Test Org 1', 'United States', 'US', NOW()),
                    ('test2', 'Test Org 2', 'Canada', NULL, NOW())
            """)
        )

        result = await author_store.migrate_country_codes()

        assert result["updated"] == 1  # Only test2 should be updated
        assert result["failed"] == 0
        assert result["total"] == 1  # Only test2 should be processed


@pytest.mark.asyncio
async def test_migrate_country_codes_with_custom_mappings(
    factory: Factory,
) -> None:
    """Test migrate_country_codes with custom country mappings."""
    async with factory.db_session.begin():
        author_store = factory.create_author_store()
        # Insert test data with custom mapping cases
        await author_store._session.execute(
            text("""
                INSERT INTO affiliation (
                    internal_id, name, address_country, date_updated
                )
                VALUES
                    ('test1', 'Test Org 1', 'USA', NOW()),
                    ('test2', 'Test Org 2', 'The Netherlands', NOW())
            """)
        )

        result = await author_store.migrate_country_codes(dry_run=True)

        assert result["updated"] == 2
        assert result["failed"] == 0
        assert result["total"] == 2

        # Verify custom mappings work
        conversions = result["conversions"]
        country_codes = [conv["country_code"] for conv in conversions.values()]
        assert "US" in country_codes  # USA -> US
        assert "NL" in country_codes  # The Netherlands -> NL
