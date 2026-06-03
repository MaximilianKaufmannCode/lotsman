# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ARQ task definitions for notification-service.

Tasks registered here:
  - dispatch_outbox:           Poll notification.outbox → push to Redis stream.
  - sync_calendar_event:       Wrap SyncCalendarEvent use case (one document at a time).
  - send_document_reminder:    Send one pre_notice/in_day/overdue email (Phase A reminders).
  - schedule_daily_reminders:  Scan registry, enqueue send_document_reminder for matches.

The ARQ worker context (ctx) must carry:
  - ctx["session_factory"]:  async_sessionmaker[AsyncSession]
  - ctx["redis"]:            aioredis.Redis
  - ctx["settings"]:         Settings
  - ctx["registry_gateway"]: HttpRegistryDocumentGateway (or None)
  - ctx["auth_gateway"]:     HttpAuthGateway (or None — only present when AUTH key wired)

cron_jobs:
  - dispatch_outbox every 5 s
  - schedule_daily_reminders @ 09:07 MSK daily (off-minute on purpose)
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import redis.asyncio as aioredis
import structlog
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notification_service.infrastructure.outbox.dispatcher import dispatch_outbox

log = structlog.get_logger(__name__)


async def sync_calendar_event(ctx: dict[str, Any], document_id: str) -> None:
    """ARQ task: synchronise calendar events for one document via EWS.

    Loads ews_config from provider_credentials (channel='exchange_calendar').
    If channel is absent or disabled, logs a warning and returns — sync is a
    no-op so the task does not retry endlessly.
    """
    from notification_service.application.use_cases.sync_calendar_event import (
        SyncCalendarEvent,
    )
    from notification_service.domain.channels import ExchangeCalendarConfig
    from notification_service.infrastructure.calendar.ews_driver import (
        EwsCalendarDriver,
    )
    from notification_service.infrastructure.channel_crypto import ChannelCipher
    from notification_service.infrastructure.db.repositories import (
        SqlaCalendarEventMappingRepository,
        SqlaCredentialRepository,
        SqlaEventOutbox,
    )

    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    settings = ctx["settings"]
    registry_gateway = ctx.get("registry_gateway")

    if registry_gateway is None:
        log.warning("sync_calendar_event.skipped", reason="registry_gateway_not_wired")
        return

    doc_uuid = uuid.UUID(document_id)
    cipher = ChannelCipher()

    async with session_factory() as session, session.begin():
        cred_repo = SqlaCredentialRepository(session)
        rows = await cred_repo.get_all()
        ews_row = next(
            (r for r in rows if r.channel == "exchange_calendar" and r.enabled),
            None,
        )
        if ews_row is None:
            log.warning(
                "sync_calendar_event.skipped",
                reason="exchange_calendar_not_configured",
                document_id=document_id,
            )
            return

        try:
            ews_config = ExchangeCalendarConfig(**cipher.decrypt(ews_row.config_enc))
        except Exception:
            log.exception("sync_calendar_event.config_decrypt_failed")
            return

        driver = EwsCalendarDriver(ews_config)
        use_case = SyncCalendarEvent(
            driver=driver,
            mapping_repo=SqlaCalendarEventMappingRepository(session),
            registry=registry_gateway,
            outbox=SqlaEventOutbox(session),
            mailbox=ews_config.target_mailbox,
            web_bff_url=settings.web_bff_url,
        )
        await use_case.execute(doc_uuid)


# ---------------------------------------------------------------------------
# Phase A — document email reminders
# ---------------------------------------------------------------------------


async def send_document_reminder(
    ctx: dict[str, Any],
    document_id: str,
    user_id: str,
    template_code: str,
    scheduled_date_iso: str,
) -> str:
    """ARQ task: send ONE reminder email for (document, user, template_code).

    Enqueued by `schedule_daily_reminders`. Idempotent — won't double-send
    on retry. Returns the final status string ('skipped' | 'sent' | 'failed').
    """
    from datetime import date as _date

    from notification_service.application.use_cases.send_document_reminder import (
        SendDocumentReminder,
    )

    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    registry_gateway = ctx.get("registry_gateway")
    auth_gateway = ctx.get("auth_gateway")
    settings = ctx["settings"]

    if registry_gateway is None or auth_gateway is None:
        log.warning(
            "send_document_reminder.skipped",
            reason="gateway_not_wired",
            doc=document_id,
        )
        return "skipped"

    web_bff_url = settings.web_bff_url

    use_case = SendDocumentReminder(
        session_factory=session_factory,
        registry_gateway=registry_gateway,
        auth_gateway=auth_gateway,
        web_bff_base_url=web_bff_url,
    )
    return await use_case.execute(
        document_id=uuid.UUID(document_id),
        user_id=uuid.UUID(user_id),
        template_code=template_code,
        scheduled_date=_date.fromisoformat(scheduled_date_iso),
    )


async def schedule_daily_reminders(ctx: dict[str, Any]) -> dict[str, int]:
    """ARQ cron task @ 09:07 MSK: scan documents, enqueue reminders.

    Returns counts for telemetry.
    """
    from notification_service.application.use_cases.schedule_daily_reminders import (
        ScheduleDailyReminders,
    )

    registry_gateway = ctx.get("registry_gateway")
    arq_pool = ctx.get("redis")  # ARQ uses the same Redis client as the pool

    if registry_gateway is None:
        log.warning(
            "schedule_daily_reminders.skipped", reason="registry_gateway_not_wired"
        )
        return {"enqueued": 0}

    # Build a fresh ArqRedis wrapper around the same Redis connection that ARQ
    # uses. `default_queue_name` must match WorkerSettings.queue_name above so
    # enqueued jobs land in this worker's own queue (not the default arq:queue
    # which causes cross-pollination with other ARQ workers).
    from arq.connections import ArqRedis

    pool = ArqRedis(
        connection_pool=arq_pool.connection_pool,
        default_queue_name="arq:notification",
    )

    use_case = ScheduleDailyReminders(
        registry_gateway=registry_gateway,
        arq_pool=pool,
        # ADR-0011 §D4: fan out reminders to all active users honoring per-user
        # prefs. Both are optional — if unset, the use case falls back to the
        # legacy responsible-only behaviour.
        auth_gateway=ctx.get("auth_gateway"),
        session_factory=ctx.get("session_factory"),
    )
    result = await use_case.execute()
    log.info("schedule_daily_reminders.completed", **result)
    return result


def _build_event_notifier(ctx: dict[str, Any]) -> Any:
    """Construct an EventNotifier from worker ctx (ADR-0011 Phase 2)."""
    from arq.connections import ArqRedis

    from notification_service.application.use_cases.event_notifications import (
        EventNotifier,
    )

    settings = ctx["settings"]
    arq_redis = ctx.get("redis")
    pool = (
        ArqRedis(
            connection_pool=arq_redis.connection_pool,
            default_queue_name="arq:notification",
        )
        if arq_redis is not None
        else None
    )
    return EventNotifier(
        session_factory=ctx["session_factory"],
        auth_gateway=ctx.get("auth_gateway"),
        registry_gateway=ctx.get("registry_gateway"),
        redis=arq_redis,
        arq_pool=pool,
        web_bff_base_url=getattr(
            settings, "web_bff_url", "https://lotsman.example.com"
        ),
    )


async def process_document_event(
    ctx: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
    actor_id: str | None,
    event_id: str | None = None,
) -> str:
    """ARQ task: turn a registry document event into user notifications."""
    if ctx.get("auth_gateway") is None or ctx.get("registry_gateway") is None:
        log.warning("process_document_event.skipped", reason="gateway_not_wired")
        return "skipped"
    notifier = _build_event_notifier(ctx)
    actor_uuid: uuid.UUID | None = None
    if actor_id:
        try:
            actor_uuid = uuid.UUID(str(actor_id))
        except ValueError:
            actor_uuid = None
    return await notifier.process_event(
        event_type=event_type,
        payload=payload,
        actor_id=actor_uuid,
        event_id=event_id,
    )


async def flush_document_update(
    ctx: dict[str, Any], document_id: str, window: int = 0
) -> str:
    """ARQ task: flush coalesced field edits for one document."""
    if ctx.get("auth_gateway") is None or ctx.get("registry_gateway") is None:
        return "skipped"
    notifier = _build_event_notifier(ctx)
    return await notifier.flush_update(uuid.UUID(document_id), window)


async def send_event_digest(ctx: dict[str, Any]) -> dict[str, int]:
    """ARQ cron: send per-user daily digest of pending event emails."""
    if ctx.get("auth_gateway") is None:
        return {"users": 0, "items": 0}
    from notification_service.application.use_cases.event_notifications import (
        SendEventDigest,
    )

    settings = ctx["settings"]
    uc = SendEventDigest(
        session_factory=ctx["session_factory"],
        auth_gateway=ctx["auth_gateway"],
        web_bff_base_url=getattr(
            settings, "web_bff_url", "https://lotsman.example.com"
        ),
    )
    result = await uc.execute()
    log.info("send_event_digest.completed", **result)
    return result


class WorkerSettings:
    """ARQ worker configuration for notification-service."""

    redis_settings = RedisSettings.from_dsn(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    )

    # Per-service queue isolation — added 2026-05-25. Before this, all 3 services
    # (auth-svc, registry-svc, notification-svc) shared the default `arq:queue`,
    # causing cross-pollination: jobs enqueued by one worker were sometimes
    # picked up by another that had no matching function → flood of
    # "function 'X' not found" failures in logs.
    queue_name = "arq:notification"

    functions = [
        dispatch_outbox,
        sync_calendar_event,
        send_document_reminder,
        schedule_daily_reminders,
        process_document_event,
        flush_document_update,
        send_event_digest,
    ]

    cron_jobs = [
        cron(
            dispatch_outbox,
            name="dispatch_outbox",
            second={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=True,
            unique=True,
        ),
        # Phase A: daily reminder scheduler at 09:07 MSK (off-minute).
        # The notification-arq container's TZ is configured by the runtime; if
        # TZ is UTC, 09:07 MSK ≈ 06:07 UTC.  cron(hour, minute, microsecond=0)
        # treats hour/minute as the container's local time. We use UTC-safe 06:07.
        cron(
            schedule_daily_reminders,
            name="schedule_daily_reminders",
            hour={6},
            minute={7},
            unique=True,
        ),
        # Phase 2: daily event-digest at 06:37 UTC (≈09:37 MSK), 30 min after
        # reminders so the two don't collide.
        cron(
            send_event_digest,
            name="send_event_digest",
            hour={6},
            minute={37},
            unique=True,
        ),
    ]

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        from notification_service.config import get_settings
        from notification_service.infrastructure.db.session import init_engine

        settings = get_settings()
        ctx["settings"] = settings

        engine = init_engine(settings.database_url)
        ctx["session_factory"] = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        ctx["redis"] = aioredis.from_url(settings.redis_url, decode_responses=False)

        registry_jwt_key = getattr(settings, "internal_jwt_key_registry", "")
        if registry_jwt_key:
            from notification_service.infrastructure.http.registry_gateway import (
                HttpRegistryDocumentGateway,
            )

            registry_url = getattr(
                settings, "registry_svc_url", "http://registry-svc:8000"
            )
            ctx["registry_gateway"] = HttpRegistryDocumentGateway(
                base_url=registry_url,
                signing_key=registry_jwt_key,
            )
        else:
            ctx["registry_gateway"] = None
            log.warning(
                "notification_arq.registry_gateway_not_wired",
                reason="internal_jwt_key_registry not configured",
            )

        # Phase A: HTTP gateway to auth-svc for bulk user lookup.
        auth_jwt_key = getattr(settings, "internal_jwt_key_auth", "")
        if auth_jwt_key:
            from notification_service.infrastructure.http.auth_gateway import (
                HttpAuthGateway,
            )

            auth_url = getattr(settings, "auth_svc_url", "http://auth-svc:8000")
            ctx["auth_gateway"] = HttpAuthGateway(
                base_url=auth_url,
                signing_key=auth_jwt_key,
            )
        else:
            ctx["auth_gateway"] = None
            log.warning(
                "notification_arq.auth_gateway_not_wired",
                reason="internal_jwt_key_auth not configured",
            )

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:
        from notification_service.infrastructure.db.session import dispose_engine

        gw = ctx.get("registry_gateway")
        if gw is not None:
            await gw.aclose()
        auth_gw = ctx.get("auth_gateway")
        if auth_gw is not None:
            await auth_gw.aclose()
        await dispose_engine()
        await ctx["redis"].aclose()
