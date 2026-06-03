# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""audit-service application factory.

Entrypoint: uvicorn audit_service.main:app --host 0.0.0.0 --port 8000

The Redis Streams consumer (audit-recorder) is started as a background
asyncio task in the lifespan. In production it should be run as a
separate process / ARQ worker; the embedded mode here is a scaffold
convenience that avoids adding a separate compose service at stage 2.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
from lotsman_shared.health import add_readiness_check, make_health_router
from lotsman_shared.logging import configure_logging
from lotsman_shared.metrics import make_metrics_router
from lotsman_shared.middleware import RequestIdMiddleware

from audit_service.api.errors import register_exception_handlers
from audit_service.api.v1 import router as v1_router
from audit_service.config import get_settings
from audit_service.infrastructure.consumer.recorder import run_consumer_loop
from audit_service.infrastructure.db.session import dispose_engine, init_engine

log = structlog.get_logger(__name__)

_consumer_task: asyncio.Task[None] | None = None
_shutdown_event: asyncio.Event | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _consumer_task, _shutdown_event
    settings = get_settings()
    configure_logging(service=settings.service_name, level=settings.log_level)
    log.info("audit_service_starting", version="0.1.0")

    engine = init_engine(settings.database_url)
    redis_client: aioredis.Redis = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url,
        encoding="utf-8",
        decode_responses=False,
    )

    async def check_postgres() -> None:
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))

    async def check_redis() -> None:
        await redis_client.ping()

    add_readiness_check("postgres", check_postgres)
    add_readiness_check("redis", check_redis)

    # Start the audit-recorder consumer as a background task.
    _shutdown_event = asyncio.Event()
    _consumer_task = asyncio.create_task(
        run_consumer_loop(redis_client, settings, _shutdown_event),
        name="audit-recorder",
    )

    yield

    log.info("audit_service_stopping")
    if _shutdown_event is not None:
        _shutdown_event.set()
    if _consumer_task is not None:
        try:
            await asyncio.wait_for(_consumer_task, timeout=5.0)
        except (TimeoutError, asyncio.CancelledError):
            _consumer_task.cancel()

    await redis_client.aclose()
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Лоцман — audit-service",
        version="0.1.0",
        description="Append-only audit event log for Лоцман (read-only API).",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)
    app.include_router(make_health_router())
    app.include_router(make_metrics_router())
    app.include_router(v1_router, prefix="/api/v1")

    return app


app = create_app()
