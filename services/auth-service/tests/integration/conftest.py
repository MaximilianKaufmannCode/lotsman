# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Integration test fixtures — Postgres + Redis via testcontainers.

Skips automatically when testcontainers cannot start (e.g., no Docker in CI).
All tests in this module require migration 0001 to have run.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

# Attempt to import testcontainers; skip the entire module if unavailable
try:
    from testcontainers.postgres import PostgresContainer
    from testcontainers.redis import RedisContainer

    _TC_AVAILABLE = True
except (ImportError, Exception):
    _TC_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _TC_AVAILABLE or os.environ.get("CI_SKIP_INTEGRATION") == "1",
    reason="testcontainers not available or CI_SKIP_INTEGRATION=1",
)

# ---------------------------------------------------------------------------
# Session-scoped containers (start once per pytest session)
# ---------------------------------------------------------------------------

if _TC_AVAILABLE:

    @pytest.fixture(scope="session")
    def pg_container():
        with PostgresContainer("postgres:16") as pg:
            yield pg

    @pytest.fixture(scope="session")
    def redis_container():
        with RedisContainer("redis:7") as r:
            yield r

    @pytest_asyncio.fixture(scope="session")
    async def async_pg_engine(pg_container):
        """Create schema via Alembic migrations (requires migration files)."""
        from sqlalchemy.ext.asyncio import create_async_engine

        url = pg_container.get_connection_url(driver="asyncpg")
        engine = create_async_engine(url, echo=False)

        try:
            import os

            import alembic.command
            import alembic.config
            from alembic.config import Config as AlembicConfig

            alembic_cfg = AlembicConfig(
                os.path.join(os.path.dirname(__file__), "../../../alembic.ini")
            )
            alembic_cfg.set_main_option(
                "sqlalchemy.url",
                pg_container.get_connection_url(driver="psycopg"),
            )
            alembic.command.upgrade(alembic_cfg, "head")
        except Exception:
            # Fallback: create tables from SQLAlchemy metadata
            from auth_service.db.models import Base

            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        yield engine
        await engine.dispose()

    @pytest_asyncio.fixture
    async def db_session(async_pg_engine):
        """Per-test transactional session with rollback."""
        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(async_pg_engine) as session, session.begin():
            yield session
            await session.rollback()

    @pytest.fixture(scope="session")
    def redis_client(redis_container):
        import redis as redis_lib

        host = redis_container.get_container_host_ip()
        port = redis_container.get_exposed_port(6379)
        return redis_lib.Redis(host=host, port=int(port), decode_responses=True)
