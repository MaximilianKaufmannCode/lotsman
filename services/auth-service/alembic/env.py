# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Alembic env.py — auth-service.

Online mode uses asyncpg (async engine).
Offline mode uses psycopg (sync) for plain SQL generation without a live DB.

Database URL is read from AUTH_DATABASE_URL environment variable.
Example:
    postgresql+asyncpg://auth_app:secret@localhost:5432/lotsman
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ---------------------------------------------------------------------------
# Alembic Config object — gives access to values in alembic.ini
# ---------------------------------------------------------------------------
config = context.config

# Populate logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# SQLAlchemy metadata for autogenerate support
# ---------------------------------------------------------------------------
# Import the models so their metadata is registered before autogenerate runs.
from auth_service.db.models import Base  # noqa: E402

target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Database URL — must come from environment, never from alembic.ini
# ---------------------------------------------------------------------------
_raw_url = os.environ.get("AUTH_DATABASE_URL")
if not _raw_url:
    raise RuntimeError(
        "AUTH_DATABASE_URL environment variable is not set. "
        "Set it before running alembic commands:\n"
        "  export AUTH_DATABASE_URL=postgresql+asyncpg://auth_app:secret@localhost:5432/lotsman"
    )

# Online async mode uses asyncpg driver.
# Offline mode needs a sync driver; swap asyncpg → psycopg for SQL generation.
_async_url = (
    _raw_url
    if "+asyncpg" in _raw_url
    else _raw_url.replace("postgresql://", "postgresql+asyncpg://")
)
_sync_url = _async_url.replace("+asyncpg", "+psycopg")

config.set_main_option("sqlalchemy.url", _async_url)


# ---------------------------------------------------------------------------
# include_schemas — restrict autogenerate to the auth schema only
# ---------------------------------------------------------------------------
def include_object(object, name, type_, reflected, compare_to):  # type: ignore[override]
    """Only autogenerate objects belonging to the auth schema."""
    if type_ == "table":
        return getattr(object, "schema", None) == "auth"
    return True


# ---------------------------------------------------------------------------
# Offline mode (SQL script generation, no live DB connection)
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=include_object,
        version_table_schema="auth",
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online async mode (default for production use)
# ---------------------------------------------------------------------------
def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        version_table_schema="auth",
        # Compare server defaults so autogenerate catches DEFAULT changes.
        compare_server_defaults=True,
    )
    with context.begin_transaction():
        # Ensure the schema exists before migrations touch it.
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS auth"))
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
