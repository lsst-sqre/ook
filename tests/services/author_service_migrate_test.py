"""Tests for AuthorService migrate_country_codes method."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ook.factory import Factory


@pytest.mark.asyncio
async def test_author_service_migrate_country_codes_delegates_to_store(
    factory: Factory,
) -> None:
    """Test that AuthorService.migrate_country_codes delegates to the store."""
    # Mock the store method
    mock_result = {
        "updated": 5,
        "failed": 1,
        "total": 6,
        "failed_conversions": [
            {"affiliation_id": 123, "country_name": "Invalid"}
        ],
        "conversions": {},
    }

    author_service = factory.create_author_service()
    with patch.object(
        author_service._author_store,
        "migrate_country_codes",
        new_callable=AsyncMock,
    ) as mock_migrate:
        mock_migrate.return_value = mock_result

        result = await author_service.migrate_country_codes(dry_run=False)

        # Verify store method was called with correct parameters
        mock_migrate.assert_called_once_with(dry_run=False)

        # Verify result is passed through
        assert result == mock_result


@pytest.mark.asyncio
async def test_author_service_migrate_country_codes_dry_run_logging(
    factory: Factory,
) -> None:
    """Test that dry run results are logged with examples."""
    mock_result = {
        "updated": 2,
        "failed": 0,
        "total": 2,
        "failed_conversions": [],
        "conversions": {
            1: {"country_name": "United States", "country_code": "US"},
            2: {"country_name": "Canada", "country_code": "CA"},
        },
    }

    author_service = factory.create_author_service()
    with patch.object(
        author_service._author_store,
        "migrate_country_codes",
        new_callable=AsyncMock,
    ) as mock_migrate:
        mock_migrate.return_value = mock_result

        with patch.object(author_service._logger, "info") as mock_log_info:
            await author_service.migrate_country_codes(dry_run=True)

            # Verify logging calls were made
            assert (
                mock_log_info.call_count >= 3
            )  # Starting, completed, and examples

            # Check for specific log messages
            call_args_list = [call[0] for call in mock_log_info.call_args_list]
            log_messages = [args[0] for args in call_args_list if args]

            assert "Starting country code migration" in log_messages
            assert "Country code migration completed" in log_messages


@pytest.mark.asyncio
async def test_author_service_migrate_country_codes_no_records(
    factory: Factory,
) -> None:
    """Test service behavior when no records need updating."""
    mock_result = {
        "updated": 0,
        "failed": 0,
        "total": 0,
        "failed_conversions": [],
        "conversions": {},
    }

    author_service = factory.create_author_service()
    with patch.object(
        author_service._author_store,
        "migrate_country_codes",
        new_callable=AsyncMock,
    ) as mock_migrate:
        mock_migrate.return_value = mock_result

        with patch.object(author_service._logger, "info") as mock_log_info:
            await author_service.migrate_country_codes()

            # Verify appropriate logging for no records
            call_args_list = [call[0] for call in mock_log_info.call_args_list]
            log_messages = [args[0] for args in call_args_list if args]

            assert "No records to update" in log_messages


@pytest.mark.asyncio
async def test_author_service_migrate_country_codes_failed_conversions_logging(
    factory: Factory,
) -> None:
    """Test logging of failed conversions."""
    mock_result = {
        "updated": 1,
        "failed": 2,
        "total": 3,
        "failed_conversions": [
            {"affiliation_id": 123, "country_name": "Invalid Country"},
            {"affiliation_id": 456, "country_name": "Another Invalid"},
        ],
        "conversions": {},
    }

    author_service = factory.create_author_service()
    with patch.object(
        author_service._author_store,
        "migrate_country_codes",
        new_callable=AsyncMock,
    ) as mock_migrate:
        mock_migrate.return_value = mock_result

        with patch.object(
            author_service._logger, "warning"
        ) as mock_log_warning:
            await author_service.migrate_country_codes()

            # Verify warning logs were called
            assert (
                mock_log_warning.call_count >= 3
            )  # Summary + individual failures

            # Check for failure summary and individual country names
            call_args_list = [
                call[0] for call in mock_log_warning.call_args_list
            ]
            log_messages = [args[0] for args in call_args_list if args]

            assert "Some country names could not be converted" in log_messages
            assert "Failed to convert country name" in log_messages
