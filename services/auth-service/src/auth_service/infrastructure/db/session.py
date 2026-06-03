# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Async SQLAlchemy engine and session factory for auth-service.

The engine is created once at startup via init_engine() called from main.py lifespan.
Use get_session() FastAPI dependency for request-scoped sessions.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str) -> AsyncEngine:
    """Create and store the module-level async engine.

    Must be called once in the application lifespan startup hook.
    """
    global _engine, _session_factory
    _engine = create_async_engine(
        database_url,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    _session_factory = async_sessionmaker(
        _engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Engine not initialised — call init_engine() first")
    return _engine


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a request-scoped async session."""
    if _session_factory is None:
        raise RuntimeError("Session factory not initialised — call init_engine() first")
    async with _session_factory() as session:
        yield session


async def dispose_engine() -> None:
    """Dispose the engine connection pool. Called in lifespan shutdown."""
    if _engine is not None:
        await _engine.dispose()
