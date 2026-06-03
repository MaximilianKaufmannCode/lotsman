# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Alembic env.py — registry-service.

Online mode uses asyncpg (async engine).
Offline mode uses psycopg (sync) for plain SQL generation without a live DB.

Database URL is read from REGISTRY_DATABASE_URL environment variable.
Example:
    postgresql+asyncpg://registry_app:secret@localhost:5432/lotsman
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from registry_service.db.models import Base  # noqa: E402

target_metadata = Base.metadata

_raw_url = os.environ.get("REGISTRY_DATABASE_URL")
if not _raw_url:
    raise RuntimeError(
        "REGISTRY_DATABASE_URL environment variable is not set. "
        "Set it before running alembic commands:\n"
        "  export REGISTRY_DATABASE_URL=postgresql+asyncpg://registry_app:secret@localhost:5432/lotsman"
    )

_async_url = (
    _raw_url
    if "+asyncpg" in _raw_url
    else _raw_url.replace("postgresql://", "postgresql+asyncpg://")
)
_sync_url = _async_url.replace("+asyncpg", "+psycopg")

config.set_main_option("sqlalchemy.url", _async_url)


def include_object(object, name, type_, reflected, compare_to):  # type: ignore[override]
    if type_ == "table":
        return getattr(object, "schema", None) == "registry"
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=include_object,
        version_table_schema="registry",
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        version_table_schema="registry",
        compare_server_defaults=True,
    )
    with context.begin_transaction():
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS registry"))
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


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
