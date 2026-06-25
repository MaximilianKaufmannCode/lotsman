# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Document lifecycle event notifications (ADR-0011 Phase 2).

Pipeline: registry.documents stream → EventNotificationConsumer enqueues
`process_document_event` → fan out to active users honoring per-user prefs:
  - in-app feed row (notification.user_notifications)
  - email: instant (send now) or digest (marked email_pending → SendEventDigest)

`document.updated.v1` fires per field, so plain field edits are COALESCED: each
edit appends the field to a Redis buffer and (re)schedules a single deferred
`flush_document_update` job per document; the flush builds ONE "изменены поля …"
notification.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import structlog

from notification_service.domain import document_events as de
from notification_service.domain.notification_prefs import effective, wants
from notification_service.infrastructure.db.repositories import (
    SqlaUserNotificationPrefRepository,
    SqlaUserNotificationRepository,
)
from notification_service.infrastructure.email_html import (
    render_markdown_subset,
    render_notification_email,
)
from notification_service.infrastructure.email_send import send_email

log = structlog.get_logger(__name__)

COALESCE_WINDOW = timedelta(minutes=10)
_COALESCE_TTL_S = int(COALESCE_WINDOW.total_seconds()) + 120
_SYSTEM_EMAIL_SUFFIX = "@system.lotsman"


@dataclass(slots=True)
class EventNotifier:
    """Delivers document-event notifications to active users per their prefs."""

    session_factory: Any
    auth_gateway: Any
    registry_gateway: Any
    redis: Any | None = None
    arq_pool: Any | None = None
    web_bff_base_url: str = "https://lotsman.example.com"

    # ── public entrypoints ────────────────────────────────────────────────

    async def process_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        actor_id: uuid.UUID | None,
        event_id: str | None = None,
    ) -> str:
        category = de.map_category(event_type, payload)
        if category is None:
            return "ignored"

        # Bulk archive → one delivery per document (dedup per event+doc).
        if event_type == "registry.document.bulk_archived.v1":
            count = 0
            for raw in payload.get("document_ids", []) or []:
                try:
                    doc_id = uuid.UUID(str(raw))
                except (ValueError, TypeError):
                    continue
                await self._deliver_for_document(
                    category=de.DOC_ARCHIVED,
                    document_id=doc_id,
                    actor_id=actor_id,
                    dedup_key=de.immediate_dedup_key(event_id, doc_id),
                )
                count += 1
            return f"delivered:{count}"

        raw_doc = payload.get("document_id")
        if not raw_doc:
            return "ignored"
        try:
            document_id = uuid.UUID(str(raw_doc))
        except (ValueError, TypeError):
            return "ignored"

        # Plain field edits → coalesce; everything else → immediate.
        if category == de.DOC_UPDATED:
            await self._buffer_update(
                document_id, payload.get("field"), actor_id, event_id
            )
            return "buffered"

        if category == de.DOC_ASSIGNED:
            after = payload.get("after")
            try:
                assignee = uuid.UUID(str(after)) if after else None
            except (ValueError, TypeError):
                assignee = None
            if assignee is None:
                return "ignored"
            await self._deliver_for_document(
                category=category,
                document_id=document_id,
                actor_id=actor_id,
                target_user_ids=[assignee],
                dedup_key=de.immediate_dedup_key(event_id, document_id),
            )
            return "delivered"

        await self._deliver_for_document(
            category=category,
            document_id=document_id,
            actor_id=actor_id,
            dedup_key=de.immediate_dedup_key(event_id, document_id),
        )
        return "delivered"

    async def flush_update(self, document_id: uuid.UUID, window: int = 0) -> str:
        """Flush coalesced field edits for one document into a single notice.

        ``window`` is the coalescing bucket the flush was scheduled for; it makes
        the in-app row idempotent (dedup_key = upd:{doc}:{window}).
        """
        if self.redis is None:
            return "no_redis"
        buf_key = f"evtbuf:{document_id}"
        actor_key = f"evtactor:{document_id}"
        fields_raw = await self.redis.lrange(buf_key, 0, -1)
        actor_raw = await self.redis.get(actor_key)
        await self.redis.delete(buf_key, actor_key)
        if not fields_raw:
            return "empty"
        fields = [f.decode() if isinstance(f, bytes) else str(f) for f in fields_raw]
        actor_id: uuid.UUID | None = None
        if actor_raw:
            val = actor_raw.decode() if isinstance(actor_raw, bytes) else str(actor_raw)
            try:
                actor_id = uuid.UUID(val) if val else None
            except ValueError:
                actor_id = None
        await self._deliver_for_document(
            category=de.DOC_UPDATED,
            document_id=document_id,
            actor_id=actor_id,
            fields=fields,
            dedup_key=de.update_dedup_key(document_id, window),
        )
        return "delivered"

    # ── internals ─────────────────────────────────────────────────────────

    async def _buffer_update(
        self,
        document_id: uuid.UUID,
        field: str | None,
        actor_id: uuid.UUID | None,
        event_id: str | None = None,
    ) -> None:
        if self.redis is None or self.arq_pool is None:
            # No coalescing infra → deliver immediately as a single-field notice.
            await self._deliver_for_document(
                category=de.DOC_UPDATED,
                document_id=document_id,
                actor_id=actor_id,
                fields=[field or "поле"],
                dedup_key=de.immediate_dedup_key(event_id, document_id),
            )
            return
        window = de.coalesce_window(time.time())
        buf_key = f"evtbuf:{document_id}"
        await self.redis.rpush(buf_key, field or "поле")
        await self.redis.expire(buf_key, _COALESCE_TTL_S)
        if actor_id is not None:
            await self.redis.set(
                f"evtactor:{document_id}", str(actor_id), ex=_COALESCE_TTL_S
            )
        # One deferred flush per (document, window). Per-window job id avoids ARQ
        # keep_result (1h) deduping a NEW window's flush against a retained
        # completed job (C2) — edits in a later window get their own flush.
        await self.arq_pool.enqueue_job(
            "flush_document_update",
            str(document_id),
            window,
            _job_id=f"flushupd:{document_id}:{window}",
            _defer_by=COALESCE_WINDOW,
            _queue_name="arq:notification",
        )

    async def _active_recipients(self) -> list[dict[str, Any]]:
        try:
            users = await self.auth_gateway.list_active_users()
        except Exception:
            log.exception("event_notifications.list_users_failed")
            return []
        return [
            u
            for u in users
            if u.get("is_active", True)
            and not str(u.get("email") or "").endswith(_SYSTEM_EMAIL_SUFFIX)
        ]

    async def _deliver_for_document(
        self,
        *,
        category: str,
        document_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        target_user_ids: list[uuid.UUID] | None = None,
        fields: list[str] | None = None,
        dedup_key: str | None = None,
    ) -> None:
        # Document label (number) + company for nicer titles / details — best-effort.
        doc_label: str | None = None
        company: str | None = None
        try:
            doc = await self.registry_gateway.get_document(document_id)
            if doc:
                doc_label = doc.get("number") or None
                company = doc.get("asset_name") or None
        except Exception:
            doc_label = None

        title = de.build_title(category, doc_label)
        body = de.build_body(category, fields=fields)
        doc_url = f"{self.web_bff_base_url}/registry?document_id={document_id}"
        settings_url = f"{self.web_bff_base_url}/profile"

        # Branded HTML (matches the deadline-reminder design) + plain-text fallback.
        details = [("Компания", company or "—"), ("№ документа", doc_label or "—")]
        body_html = render_notification_email(
            subject=f"Лоцман: {title}",
            headline=title,
            intro_html=render_markdown_subset(body) if body else "",
            details=details,
            cta_url=doc_url,
            settings_url=settings_url,
            status="info",
        )
        detail_lines = "\n".join(f"{k}: {v}" for k, v in details if v and v != "—")
        body_text = (
            f"{body}\n\n{detail_lines}\n\nОткрыть документ:\n{doc_url}\n\n"
            f"Настроить уведомления: {settings_url}\n\n— Лоцман"
        )

        recipients = await self._active_recipients()
        if target_user_ids is not None:
            allow = {str(u) for u in target_user_ids}
            recipients = [u for u in recipients if str(u.get("id")) in allow]
        if not recipients:
            return

        async with self.session_factory() as session:
            prefs_repo = SqlaUserNotificationPrefRepository(session)
            prefs = {r.user_id: r for r in await prefs_repo.list_all()}

        instant: list[tuple[uuid.UUID, str]] = []  # (user_id, email) to send now
        async with self.session_factory() as session, session.begin():
            notif_repo = SqlaUserNotificationRepository(session)
            for u in recipients:
                try:
                    uid = uuid.UUID(str(u["id"]))
                except (KeyError, ValueError):
                    continue
                email = u.get("email")
                row = prefs.get(uid)
                eff = effective(row)
                if not eff.enabled:
                    continue
                if actor_id is not None and uid == actor_id and eff.suppress_own:
                    continue
                want_inapp = wants(row, category, "in_app")
                want_email = wants(row, category, "email")
                if want_inapp:
                    pending = want_email and eff.email_mode == "digest"
                    inserted = await notif_repo.add(
                        user_id=uid,
                        category=category,
                        title=title,
                        body=body,
                        document_id=document_id,
                        actor_id=actor_id,
                        email_pending=pending,
                        dedup_key=dedup_key,
                    )
                    # Only email on a FRESH insert — a suppressed duplicate
                    # (redelivery) must not re-send the instant email (C1).
                    if inserted and want_email and eff.email_mode == "instant" and email:
                        instant.append((uid, email))
                elif want_email and email and eff.email_mode != "off":
                    # No feed row to defer against → send now (best-effort).
                    instant.append((uid, email))

        # Send instant emails + record each in delivery_attempts (C3 visibility).
        for uid, to in instant:
            ok, err = await send_email(
                session_factory=self.session_factory,
                to=to,
                subject=f"Лоцман: {title}",
                body_text=body_text,
                body_html=body_html,
            )
            if not ok:
                log.warning("event_notifications.instant_email_failed", to=to, error=err)
            async with self.session_factory() as session, session.begin():
                await SqlaUserNotificationRepository(session).record_delivery_attempt(
                    document_id=document_id,
                    user_id=uid,
                    template_code=category,
                    status="sent" if ok else "failed",
                    error=err,
                )


@dataclass(slots=True)
class SendEventDigest:
    """Daily digest: one email per user summarising email_pending feed items."""

    session_factory: Any
    auth_gateway: Any
    web_bff_base_url: str = "https://lotsman.example.com"

    async def execute(self) -> dict[str, int]:
        async with self.session_factory() as session:
            notif_repo = SqlaUserNotificationRepository(session)
            pending = await notif_repo.list_email_pending(limit=1000)

        if not pending:
            return {"users": 0, "items": 0}

        # Group by recipient.
        by_user: dict[uuid.UUID, list[Any]] = {}
        for row in pending:
            by_user.setdefault(row.user_id, []).append(row)

        # Resolve emails.
        try:
            users = await self.auth_gateway.list_active_users()
        except Exception:
            log.exception("event_digest.list_users_failed")
            return {"users": 0, "items": 0}
        email_by_id: dict[str, str] = {
            str(u.get("id")): str(u.get("email"))
            for u in users
            if u.get("is_active", True) and u.get("email")
        }

        sent_users = 0
        sent_items = 0
        for uid, rows in by_user.items():
            to = email_by_id.get(str(uid))
            done_ids = [r.id for r in rows]
            if not to:
                # User inactive/unknown → just clear the flag so it won't pile up.
                async with self.session_factory() as session, session.begin():
                    await SqlaUserNotificationRepository(session).mark_emailed(done_ids)
                continue
            registry_url = f"{self.web_bff_base_url}/registry"
            settings_url = f"{self.web_bff_base_url}/profile"
            lines = [f"• {r.title} — {r.body}" for r in rows]
            tail = (
                f"\n\nОткрыть реестр:\n{registry_url}"
                f"\n\nНастроить уведомления: {settings_url}\n\n— Лоцман"
            )
            body = "Сводка событий по документам реестра:\n\n" + "\n".join(lines) + tail
            intro_md = "За день накопились события по документам реестра:\n\n" + "\n\n".join(
                f"**{r.title}** — {r.body}" for r in rows
            )
            body_html = render_notification_email(
                subject=f"Лоцман: сводка событий ({len(rows)})",
                headline=f"Сводка событий за день ({len(rows)})",
                intro_html=render_markdown_subset(intro_md),
                cta_url=registry_url,
                cta_label="Открыть реестр",
                settings_url=settings_url,
                status="info",
            )
            ok, err = await send_email(
                session_factory=self.session_factory,
                to=to,
                subject=f"Лоцман: сводка событий ({len(rows)})",
                body_text=body,
                body_html=body_html,
            )
            if ok:
                async with self.session_factory() as session, session.begin():
                    await SqlaUserNotificationRepository(session).mark_emailed(done_ids)
                sent_users += 1
                sent_items += len(rows)
            else:
                log.warning("event_digest.email_failed", to=to, error=err)
        return {"users": sent_users, "items": sent_items}
