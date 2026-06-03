# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Async SQLAlchemy engine and session factory for audit-service."""

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


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the module-level session factory.

    Used by the audit-recorder consumer (which is not request-scoped and
    cannot use the FastAPI Depends-based ``get_session`` generator).
    Must be called after ``init_engine``.
    """
    if _session_factory is None:
        raise RuntimeError("Session factory not initialised — call init_engine() first")
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if _session_factory is None:
        raise RuntimeError("Session factory not initialised — call init_engine() first")
    async with _session_factory() as session:
        yield session


async def dispose_engine() -> None:
    if _engine is not None:
        await _engine.dispose()
