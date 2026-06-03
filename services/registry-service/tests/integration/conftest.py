# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Integration test fixtures for registry-service.

Uses testcontainers-python to spin up a real PostgreSQL 16 instance.
The database schema is created from the ORM Base metadata so tests run
without Alembic (avoids needing a running migration environment in CI).

Fixture scoping:
  - pg_container  — session-scoped: one PG container for the whole test run
  - engine        — session-scoped: one engine
  - create_tables — session-scoped autouse: DDL once
  - session       — function-scoped: one async session per test, rolled back on teardown

Requirements:
  pip install testcontainers[postgres] asyncpg sqlalchemy[asyncio] pytest-asyncio

Skip conditions (set by pytestmark at module level):
  - pytest.importorskip handles missing testcontainers gracefully
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

try:
    from testcontainers.postgres import PostgresContainer

    _TC_AVAILABLE = True
except ImportError:
    _TC_AVAILABLE = False

try:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    _SQLA_AVAILABLE = True
except ImportError:
    _SQLA_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _TC_AVAILABLE or not _SQLA_AVAILABLE,
    reason=(
        "testcontainers[postgres] or sqlalchemy[asyncio] not installed. "
        "Install with: uv add --dev testcontainers[postgres] asyncpg"
    ),
)


@pytest.fixture(scope="session")
def pg_container():
    """Session-scoped PostgreSQL 16 container."""
    if not _TC_AVAILABLE:
        pytest.skip("testcontainers not available")
    with PostgresContainer("postgres:16", driver="asyncpg") as pg:
        yield pg


@pytest.fixture(scope="session")
def engine(pg_container):
    """Session-scoped async SQLAlchemy engine connected to the test container."""
    url = pg_container.get_connection_url()
    # Replace psycopg2 driver with asyncpg for async support
    url = url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    eng = create_async_engine(url, poolclass=NullPool, echo=False)
    yield eng

    async def dispose():
        await eng.dispose()

    asyncio.get_event_loop().run_until_complete(dispose())


@pytest.fixture(scope="session", autouse=True)
def create_tables(engine, pg_container):
    """Create the registry schema and all tables once per session."""
    from registry_service.db.models import Base

    async def _create():
        async with engine.begin() as conn:
            # Create the registry schema (not done by Base.metadata.create_all by default)
            await conn.execute(
                __import__("sqlalchemy").text("CREATE SCHEMA IF NOT EXISTS registry")
            )
            # Enable pg_trgm extension (needed for similarity queries)
            await conn.execute(
                __import__("sqlalchemy").text("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            )
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_create())


@pytest_asyncio.fixture
async def session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Function-scoped async session. Rolls back after each test for isolation."""
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionFactory() as s:
        # Begin a nested (SAVEPOINT) transaction so rollback resets the DB state
        async with s.begin():
            try:
                yield s
            finally:
                await s.rollback()


@pytest.fixture
def fake_outbox():
    """In-memory outbox for tracking published events in integration tests."""
    from tests.unit.use_cases.fakes import FakeEventOutbox

    return FakeEventOutbox()


@pytest.fixture
def clock():
    """Fixed-time clock for integration tests."""
    from datetime import UTC, date, datetime

    from tests.unit.use_cases.fakes import FakeClock

    return FakeClock(
        fixed_dt=datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
        fixed_date=date(2026, 5, 7),
    )
