# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ScheduleDailyReminders use case — runs daily, enqueues reminder tasks.

Logic:
  - pre_notice: days_to_expiry IN ANY(pre_notice_days)
  - in_day:     days_to_expiry == 0 AND notify_in_day == TRUE
  - overdue:    days_to_expiry < 0 AND |days_to_expiry| % overdue_every_days == 0

Cross-service data: only via HTTP gateways (no cross-schema DB access).

Returns total number of reminders enqueued.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from notification_service.domain.notification_prefs import wants
from notification_service.infrastructure.db.repositories import (
    SqlaUserNotificationPrefRepository,
)
from notification_service.infrastructure.http.registry_gateway import (
    HttpRegistryDocumentGateway,
)
from notification_service.infrastructure.metrics import (
    SCHEDULE_ENQUEUED_TOTAL,
    SCHEDULE_RUN_TOTAL,
    SCHEDULE_SKIPPED_TOTAL,
)

log = logging.getLogger(__name__)

# System service accounts that must never receive notifications.
_SYSTEM_EMAIL_SUFFIX = "@system.lotsman"


@dataclass(slots=True)
class ScheduleDailyReminders:
    registry_gateway: HttpRegistryDocumentGateway
    arq_pool: Any  # arq.connections.ArqRedis
    # When set (ADR-0011 §D4), reminders fan out to ALL active users (honoring
    # per-user prefs) instead of only the document's responsible user.
    auth_gateway: Any | None = None  # HttpAuthGateway
    session_factory: Any | None = None  # async_sessionmaker — to read prefs

    async def execute(self, *, today: date | None = None) -> dict[str, int]:
        if today is None:
            today = date.today()

        # 1. Fetch all active documents w/ expiry_date set
        try:
            documents = await self.registry_gateway.list_active_documents()
        except Exception:
            log.exception("schedule_daily_reminders.list_documents_failed")
            SCHEDULE_RUN_TOTAL.labels(outcome="error").inc()
            return {
                "enqueued": 0,
                "skipped_no_user": 0,
                "skipped_no_expiry": 0,
                "skipped_archived": 0,
            }

        # 1a. Defensive filter: registry's `include_archived=false` only filters by
        #     deleted_at IS NULL (soft-delete), not by status='archived'. We must
        #     explicitly exclude archived/non-active documents — they're intentionally
        #     out-of-rotation, reminders would be noise. (User requirement 2026-05-25.)
        documents_active = [d for d in documents if (d.get("status") or "active") == "active"]
        skipped_archived = len(documents) - len(documents_active)
        documents = documents_active

        # 2. Build doc_type cache (fetch unique types once)
        type_codes = {d.get("type_code") or d.get("type") for d in documents}
        type_codes.discard(None)
        type_cache: dict[str, dict[str, Any]] = {}
        for tc in type_codes:
            if not tc:
                continue
            tc_str = str(tc)
            try:
                row = await self.registry_gateway.get_document_type(tc_str)
                if row:
                    type_cache[tc_str] = row
            except Exception:
                continue

        enqueued = 0
        skipped_no_user = 0
        skipped_no_expiry = 0

        # 3. For each document, compute template_code if any → collect due items
        #    as (doc_uuid, template_code, responsible_user_id).
        due: list[tuple[uuid.UUID, str, str | None]] = []
        for doc in documents:
            expiry_iso = doc.get("expiry_date") or doc.get("expires_at")
            if not expiry_iso:
                skipped_no_expiry += 1
                continue
            try:
                expiry = date.fromisoformat(str(expiry_iso)[:10])
            except ValueError:
                skipped_no_expiry += 1
                continue

            days_to_expiry = (expiry - today).days

            tc = doc.get("type_code") or doc.get("type")
            type_cfg = type_cache.get(tc, {}) if tc else {}
            pre_notice_days: list[int] = list(type_cfg.get("pre_notice_days") or [])
            notify_in_day: bool = bool(type_cfg.get("notify_in_day", False))
            overdue_every_days: int = int(type_cfg.get("overdue_every_days") or 0)

            template_code: str | None = None
            if days_to_expiry in pre_notice_days:
                template_code = "pre_notice"
            elif days_to_expiry == 0 and notify_in_day:
                template_code = "in_day"
            elif (
                days_to_expiry < 0
                and overdue_every_days > 0
                and (-days_to_expiry) % overdue_every_days == 0
            ):
                template_code = "overdue"

            if template_code is None:
                continue

            try:
                doc_uuid = uuid.UUID(str(doc["id"]))
            except (KeyError, ValueError):
                continue

            due.append((doc_uuid, template_code, doc.get("responsible_user_id")))

        # 4. Resolve recipients. With auth_gateway wired → ALL active users that
        #    haven't opted out of deadline emails (ADR-0011 §D4). Otherwise fall
        #    back to the legacy responsible-only behaviour (data-safe default).
        recipients = await self._resolve_recipients()

        # 5. Enqueue one reminder per (document, recipient). Idempotency job id is
        #    per-user so two users' reminders for the same document don't collide;
        #    send_document_reminder dedups on (doc, user, template, date, sent).
        for doc_uuid, template_code, resp_user_id in due:
            if recipients is None:
                # legacy: responsible only
                targets = []
                if resp_user_id:
                    try:
                        targets = [uuid.UUID(str(resp_user_id))]
                    except ValueError:
                        targets = []
            else:
                targets = recipients

            if not targets:
                skipped_no_user += 1
                continue

            for user_uuid in targets:
                await self.arq_pool.enqueue_job(
                    "send_document_reminder",
                    str(doc_uuid),
                    str(user_uuid),
                    template_code,
                    today.isoformat(),
                    _job_id=(
                        f"reminder:{doc_uuid}:{user_uuid}:{template_code}:"
                        f"{today.isoformat()}"
                    ),
                )
                enqueued += 1
                SCHEDULE_ENQUEUED_TOTAL.labels(template_code=template_code).inc()

        # Bump skip counters in batches (Prometheus Counter.inc supports amount)
        if skipped_no_user:
            SCHEDULE_SKIPPED_TOTAL.labels(reason="no_user").inc(skipped_no_user)
        if skipped_no_expiry:
            SCHEDULE_SKIPPED_TOTAL.labels(reason="no_expiry").inc(skipped_no_expiry)
        if skipped_archived:
            SCHEDULE_SKIPPED_TOTAL.labels(reason="archived").inc(skipped_archived)
        SCHEDULE_RUN_TOTAL.labels(outcome="ok").inc()

        log.info(
            "schedule_daily_reminders.done: enqueued=%d skipped_no_user=%d "
            "skipped_no_expiry=%d skipped_archived=%d",
            enqueued,
            skipped_no_user,
            skipped_no_expiry,
            skipped_archived,
        )
        return {
            "enqueued": enqueued,
            "skipped_no_user": skipped_no_user,
            "skipped_no_expiry": skipped_no_expiry,
            "skipped_archived": skipped_archived,
        }

    async def _resolve_recipients(self) -> list[uuid.UUID] | None:
        """Active human users that want deadline emails (ADR-0011 §D4).

        Returns ``None`` to signal the legacy responsible-only fallback when the
        auth gateway is not wired or the user list cannot be fetched — this keeps
        reminders flowing even if the new path is misconfigured (data-safe).
        """
        if self.auth_gateway is None:
            return None

        try:
            users = await self.auth_gateway.list_active_users()
        except Exception:
            log.exception("schedule_daily_reminders.list_users_failed")
            return None

        # An empty active-user list is implausible (there is always ≥1 admin) and
        # almost certainly means the auth call failed silently. Fall back to the
        # legacy responsible-only path rather than silently dropping ALL reminders.
        if not users:
            log.warning("schedule_daily_reminders.empty_user_list_fallback")
            return None

        # Load all prefs once (small table — 2–4 rows in practice).
        prefs_by_user: dict[uuid.UUID, Any] = {}
        if self.session_factory is not None:
            try:
                async with self.session_factory() as session:
                    repo = SqlaUserNotificationPrefRepository(session)
                    for row in await repo.list_all():
                        prefs_by_user[row.user_id] = row
            except Exception:
                log.exception("schedule_daily_reminders.load_prefs_failed")
                # Fail open: with no prefs, defaults apply (deadline email on).

        out: list[uuid.UUID] = []
        for u in users:
            if not u.get("is_active", True):
                continue
            email = str(u.get("email") or "")
            if email.endswith(_SYSTEM_EMAIL_SUFFIX):
                continue
            try:
                uid = uuid.UUID(str(u["id"]))
            except (KeyError, ValueError):
                continue
            if wants(prefs_by_user.get(uid), "deadline", "email"):
                out.append(uid)
        return out
