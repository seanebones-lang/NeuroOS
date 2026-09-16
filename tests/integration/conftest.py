"""PostgreSQL-backed fixtures for API integration tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from neuro_os import api as api_module
from neuro_os.api import app, get_memory_manager
from neuro_os.database import get_session
from neuro_os.models import Base


class InMemoryRedis:
    """Minimal limiter state for deterministic API integration tests."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def ttl(self, key: str) -> int:
        return self.expirations.get(key, -1)

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations[key] = seconds

    async def aclose(self) -> None:
        return None


def _integration_database_url() -> str:
    url = os.getenv("NEURO_OS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NEURO_OS_TEST_DATABASE_URL is not configured")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("NEURO_OS_TEST_DATABASE_URL must use PostgreSQL with asyncpg")
    return url


@pytest_asyncio.fixture
async def integration_engine() -> AsyncIterator[AsyncEngine]:
    """Connect only to a migrated, explicitly configured PostgreSQL test database."""
    engine = create_async_engine(_integration_database_url())
    expected_revision = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    async with engine.connect() as connection:
        actual_revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    if actual_revision != expected_revision:
        await engine.dispose()
        pytest.fail(
            f"PostgreSQL test database is at {actual_revision!r}; expected {expected_revision!r}"
        )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_database(integration_engine: AsyncEngine) -> AsyncIterator[None]:
    """Give each integration test an empty database without changing migration state."""
    table_names = [table.name for table in Base.metadata.sorted_tables]
    quoted_tables = ", ".join(f'"{name}"' for name in table_names)
    async with integration_engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {quoted_tables} RESTART IDENTITY CASCADE"))
    yield


@pytest_asyncio.fixture
async def api_client(integration_engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    """Exercise the FastAPI app with one database session per request."""
    session_factory = async_sessionmaker(
        integration_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_memory_manager() -> None:
        return None

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_memory_manager] = override_memory_manager
    api_module._redis_client = InMemoryRedis()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        api_module._redis_client = None
        app.dependency_overrides.clear()


@pytest.fixture
def integration_session_factory(
    integration_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        integration_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
