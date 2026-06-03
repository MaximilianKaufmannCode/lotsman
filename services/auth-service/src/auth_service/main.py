# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""auth-service application factory.

Entrypoint: uvicorn auth_service.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
from lotsman_shared.health import add_readiness_check, make_health_router
from lotsman_shared.logging import configure_logging
from lotsman_shared.metrics import make_metrics_router
from lotsman_shared.middleware import RequestIdMiddleware

from auth_service.api.errors import register_exception_handlers
from auth_service.api.v1 import router as v1_router
from auth_service.config import get_settings
from auth_service.infrastructure.db.session import dispose_engine, init_engine

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(service=settings.service_name, level=settings.log_level)
    log.info("auth_service_starting", version="0.1.0")

    engine = init_engine(settings.database_url)

    # Redis client — shared singleton, mounted on app.state for dep injection
    redis_client: aioredis.Redis = aioredis.from_url(  # type: ignore[type-arg]
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    app.state.redis = redis_client

    async def check_postgres() -> None:
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))

    async def check_redis() -> None:
        await redis_client.ping()

    add_readiness_check("postgres", check_postgres)
    add_readiness_check("redis", check_redis)

    yield

    log.info("auth_service_stopping")
    await redis_client.aclose()
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Лоцман — auth-service",
        version="0.1.0",
        description="Authentication, sessions, TOTP, JWT for Лоцман.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # Middleware (applied outermost-first)
    app.add_middleware(RequestIdMiddleware)

    # Exception handlers
    register_exception_handlers(app)

    # Routers
    app.include_router(make_health_router())
    app.include_router(make_metrics_router())
    app.include_router(v1_router, prefix="/api/v1")

    return app


app = create_app()
