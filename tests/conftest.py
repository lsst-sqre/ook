"""Pytest configuration for the ``ook`` app."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def http_client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient() as client:
        yield client
