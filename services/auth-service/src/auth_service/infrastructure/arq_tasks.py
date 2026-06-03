# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ARQ task definitions for auth-service.

Tasks registered here:
  - dispatch_outbox: poll auth.outbox → push events to Redis Streams (one
    stream per topic, e.g. auth.user, auth.session, auth.invite).

The ARQ worker context (ctx) must carry:
  - ctx["session_factory"]: async_sessionmaker[AsyncSession]
  - ctx["redis"]:           aioredis.Redis

cron_jobs:
  - dispatch_outbox every 1 s (low-latency invite delivery)
"""

from __future__ import annotations

import os
from typing import Any

import redis.asyncio as aioredis
import structlog
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from auth_service.infrastructure.outbox.dispatcher import dispatch_outbox as _dispatch_outbox_impl

log = structlog.get_logger(__name__)


# Wrap with a unique function name so the ARQ cron `unique=True` lock does
# not collide with notification-arq's `dispatch_outbox` (both workers share
# the same Redis instance).
async def auth_dispatch_outbox(ctx: dict[str, Any]) -> None:
    await _dispatch_outbox_impl(ctx)


class WorkerSettings:
    """ARQ worker configuration for auth-service."""

    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))

    # Per-service queue isolation (see notification-service WorkerSettings for context).
    # Before 2026-05-25 all 3 services shared the default arq:queue, causing
    # cross-pollination — jobs of one service ran on another worker that didn't
    # have the matching function → "function not found" floods in logs.
    queue_name = "arq:auth"

    functions = [auth_dispatch_outbox]

    cron_jobs = [
        cron(
            auth_dispatch_outbox,
            name="auth_dispatch_outbox",
            second={
                0,
                2,
                4,
                6,
                8,
                10,
                12,
                14,
                16,
                18,
                20,
                22,
                24,
                26,
                28,
                30,
                32,
                34,
                36,
                38,
                40,
                42,
                44,
                46,
                48,
                50,
                52,
                54,
                56,
                58,
            },
            run_at_startup=True,
            unique=True,
        ),
    ]

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        from auth_service.config import get_settings
        from auth_service.infrastructure.db.session import init_engine

        settings = get_settings()
        ctx["settings"] = settings

        engine = init_engine(settings.database_url)
        ctx["session_factory"] = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        ctx["redis"] = aioredis.from_url(settings.redis_url, decode_responses=False)
        log.info("auth_arq.started")

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:
        from auth_service.infrastructure.db.session import dispose_engine

        await dispose_engine()
        await ctx["redis"].aclose()
        log.info("auth_arq.stopped")
