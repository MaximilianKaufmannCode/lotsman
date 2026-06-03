# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""notification-service application factory.

Entrypoint: uvicorn notification_service.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from typing import Any

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
from lotsman_shared.health import add_readiness_check, make_health_router
from lotsman_shared.logging import configure_logging
from lotsman_shared.metrics import make_metrics_router
from lotsman_shared.middleware import RequestIdMiddleware

from notification_service.api.errors import register_exception_handlers
from notification_service.api.v1 import router as v1_router
from notification_service.api.v1.calendar_feed import invalidate_ics_cache
from notification_service.config import get_settings
from notification_service.infrastructure.channel_crypto import ChannelCipher
from notification_service.infrastructure.db.session import (
    dispose_engine,
    get_session,
    init_engine,
)

log = structlog.get_logger(__name__)


async def _warm_up_reconciliation(app: FastAPI) -> None:
    """ADR-0005 §12: enqueue sync tasks for stale pending/failed mappings.

    Runs as a background task — does NOT block startup.
    """
    await asyncio.sleep(5)  # Let the service finish booting.
    log.info("warm_up_reconciliation.started")
    try:
        from notification_service.infrastructure.db.repositories import (
            SqlaCalendarEventMappingRepository,
        )

        async for session in get_session():
            repo = SqlaCalendarEventMappingRepository(session)
            stale = await repo.list_stale(
                states=["pending", "failed"],
                older_than_minutes=15,
                limit=1000,
            )

        if stale:
            enqueue = getattr(app.state, "calendar_sync_enqueue", None)
            if enqueue is not None:
                for mapping in stale:
                    await enqueue(mapping.document_id)
                log.info("warm_up_reconciliation.enqueued", count=len(stale))
    except Exception:
        log.exception("warm_up_reconciliation.failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(service=settings.service_name, level=settings.log_level)
    log.info("notification_service_starting", version="0.1.0")

    # US-16 scenario 2: refuse to boot without CHANNEL_ENC_KEY.
    # ChannelCipher reads the env var directly; raises RuntimeError if missing.
    try:
        ChannelCipher()
    except RuntimeError as exc:
        log.error("startup_failed_missing_enc_key", error=str(exc))
        raise

    engine = init_engine(settings.database_url)

    async def check_postgres() -> None:
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))

    add_readiness_check("postgres", check_postgres)

    # Wire Redis into app.state so API deps can access it (F-009 / Blocker 4).
    redis_client: aioredis.Redis = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    app.state.redis = redis_client

    # Wire registry HTTP gateway for ICS feed and calendar sync.
    registry_url = getattr(settings, "registry_svc_url", "http://registry-svc:8000")
    registry_jwt_key = getattr(settings, "internal_jwt_key_registry", "")
    if registry_jwt_key:
        from notification_service.infrastructure.http.registry_gateway import (
            HttpRegistryDocumentGateway,
        )

        app.state.registry_gateway = HttpRegistryDocumentGateway(
            base_url=registry_url,
            signing_key=registry_jwt_key,
        )
    else:
        app.state.registry_gateway = None
        log.warning(
            "notification_service.registry_gateway_not_wired",
            reason="internal_jwt_key_registry not configured",
        )

    # ARQ pool for enqueueing sync_calendar_event jobs.
    from arq import create_pool
    from arq.connections import RedisSettings

    arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    app.state.arq_pool = arq_pool

    async def _enqueue_calendar_sync(document_id: uuid.UUID) -> None:
        await arq_pool.enqueue_job("sync_calendar_event", str(document_id))
        log.info("calendar_sync.enqueued", document_id=str(document_id))

    app.state.calendar_sync_enqueue = _enqueue_calendar_sync

    # Start registry document consumer as a background task.
    consumer_task: asyncio.Task[None] | None = None
    if registry_jwt_key:
        from notification_service.infrastructure.consumers.registry_document_consumer import (
            RegistryDocumentConsumer,
        )

        async def _invalidate_cache() -> None:
            invalidate_ics_cache()

        consumer = RegistryDocumentConsumer(
            redis_client=redis_client,
            arq_enqueue=_enqueue_calendar_sync,
            ics_cache_invalidate=_invalidate_cache,
        )
        consumer_task = asyncio.create_task(consumer.start())
        log.info("notification_service.consumer_started")

    # Start event-notification consumer (ADR-0011 Phase 2) — INDEPENDENT consumer
    # group on the same registry.documents stream, so it cannot disturb calendar
    # sync. Enqueues process_document_event onto the notification worker queue.
    event_consumer_task: asyncio.Task[None] | None = None
    if registry_jwt_key:
        from notification_service.infrastructure.consumers.event_notification_consumer import (  # noqa: E501
            EventNotificationConsumer,
        )

        async def _enqueue_event(
            event_type: str,
            payload: dict[str, Any],
            actor_id: Any,
            event_id: Any = None,
        ) -> None:
            await arq_pool.enqueue_job(
                "process_document_event",
                event_type,
                payload,
                str(actor_id) if actor_id else None,
                str(event_id) if event_id else None,
                _queue_name="arq:notification",
            )

        event_consumer = EventNotificationConsumer(
            redis_client=redis_client, enqueue_event=_enqueue_event
        )
        event_consumer_task = asyncio.create_task(event_consumer.start())
        log.info("notification_service.event_consumer_started")

    # Start invite consumer (auth.invite stream → email).
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from notification_service.infrastructure.consumers.invite_consumer import (
        InviteConsumer,
    )
    from notification_service.infrastructure.db.session import get_engine

    invite_session_factory = async_sessionmaker(
        get_engine(), expire_on_commit=False
    )
    invite_consumer = InviteConsumer(
        redis_client=redis_client,
        session_factory=invite_session_factory,
        web_bff_url=getattr(settings, "web_bff_url", ""),
    )
    invite_consumer_task: asyncio.Task[None] | None = asyncio.create_task(
        invite_consumer.start()
    )
    log.info("notification_service.invite_consumer_started")

    # Tier B: warm-up reconciliation (non-blocking).
    asyncio.create_task(_warm_up_reconciliation(app))

    yield

    log.info("notification_service_stopping")
    if consumer_task is not None:
        consumer_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await consumer_task
    if event_consumer_task is not None:
        event_consumer_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await event_consumer_task
    if invite_consumer_task is not None:
        invite_consumer.stop()
        invite_consumer_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await invite_consumer_task
    gateway = getattr(app.state, "registry_gateway", None)
    if gateway is not None:
        await gateway.aclose()
    pool = getattr(app.state, "arq_pool", None)
    if pool is not None:
        await pool.aclose()
    await redis_client.aclose()
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Лоцман — notification-service",
        version="0.1.0",
        description="Delivery rules, attempts, templates, and provider dispatch for Лоцман.",
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
