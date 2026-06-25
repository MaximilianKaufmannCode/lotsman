# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""SendDocumentReminder use case — pre_notice / in_day / overdue email per document.

Орchestrates:
  1. Idempotency check via notification.delivery_attempts
     (skip if status='sent' already exists for this (doc, template, user, date)).
  2. Fetch document via registry-gateway.
  3. Fetch document_type via registry-gateway.
  4. Fetch user via auth-gateway.
  5. Load message template from DB (channel='email', template_code, locale='ru').
  6. Render via templating.render_template.
  7. Send via _send_transactional_smart (reuse SMTP+EWS-fallback path).
  8. Update delivery_attempts row (pending → sent or failed + error).

Cross-service data flow uses HTTP gateways; no cross-schema DB SELECT.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notification_service.api.v1.admin_channels import _cipher
from notification_service.api.v1.internal_email import _send_transactional_smart
from notification_service.db.models import DeliveryAttempt, MessageTemplate
from notification_service.infrastructure.db.repositories import SqlaCredentialRepository
from notification_service.infrastructure.email_html import (
    render_markdown_subset,
    render_notification_email,
)
from notification_service.infrastructure.http.auth_gateway import HttpAuthGateway
from notification_service.infrastructure.http.registry_gateway import (
    HttpRegistryDocumentGateway,
)
from notification_service.infrastructure.humanize import days_phrase, format_date_ru
from notification_service.infrastructure.metrics import (
    EMAIL_REMINDERS_TOTAL,
    EMAIL_SEND_DURATION,
)
from notification_service.infrastructure.templating import (
    TemplateRenderError,
    render_template,
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SendDocumentReminder:
    session_factory: async_sessionmaker[AsyncSession]
    registry_gateway: HttpRegistryDocumentGateway
    auth_gateway: HttpAuthGateway
    web_bff_base_url: str = "https://lotsman.example.com"

    async def execute(
        self,
        *,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        template_code: str,
        scheduled_date: date,
    ) -> str:
        """Send a single reminder. Returns final status: 'skipped' | 'sent' | 'failed'."""
        result = await self._execute_inner(
            document_id=document_id,
            user_id=user_id,
            template_code=template_code,
            scheduled_date=scheduled_date,
        )
        EMAIL_REMINDERS_TOTAL.labels(
            status=result, channel="email", template_code=template_code
        ).inc()
        return result

    async def _execute_inner(
        self,
        *,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        template_code: str,
        scheduled_date: date,
    ) -> str:
        """Inner — metric wrapping done by `execute`."""
        # 1. Idempotency: has this exact reminder already been sent today?
        async with self.session_factory() as session:
            existing = await session.execute(
                select(DeliveryAttempt).where(
                    DeliveryAttempt.document_id == document_id,
                    DeliveryAttempt.user_id == user_id,
                    DeliveryAttempt.template_code == template_code,
                    DeliveryAttempt.channel == "email",
                    DeliveryAttempt.scheduled_at.cast(__import__("sqlalchemy").Date)
                    == scheduled_date,
                    DeliveryAttempt.status == "sent",
                )
            )
            if existing.scalar_one_or_none() is not None:
                log.info(
                    "send_document_reminder.skipped_already_sent: doc=%s user=%s tpl=%s",
                    document_id,
                    user_id,
                    template_code,
                )
                return "skipped"

        # 2. Fetch document + type
        try:
            document = await self.registry_gateway.get_document(document_id)
        except Exception as exc:
            log.warning("send_document_reminder.fetch_document_failed: %s", exc)
            return "failed"
        if document is None:
            log.info(
                "send_document_reminder.skipped_document_missing: doc=%s", document_id
            )
            return "skipped"

        # 2a. Defensive: skip archived/non-active documents. Per user requirement
        #     2026-05-25, archived documents are out-of-rotation and must NOT
        #     receive reminders. Scheduler also filters this, but enforce here
        #     too — defense-in-depth for manual enqueues / external callers.
        doc_status = document.get("status") or "active"
        if doc_status != "active":
            log.info(
                "send_document_reminder.skipped_archived: doc=%s status=%s",
                document_id,
                doc_status,
            )
            return "skipped"

        # 3. Fetch user
        users = await self.auth_gateway.lookup_users([user_id])
        user = users.get(user_id)
        if user is None or not user.get("email"):
            log.info(
                "send_document_reminder.skipped_user_missing: doc=%s user=%s",
                document_id,
                user_id,
            )
            return "skipped"

        # 3a. Defensive: skip if user is deactivated. Deactivated users cannot
        #     log in and may have lost mailbox access; sending reminders is
        #     wasteful and may confuse the recipient. auth_gateway.lookup_users
        #     returns is_active in payload — we trust that.
        if user.get("is_active") is False:
            log.info(
                "send_document_reminder.skipped_user_inactive: doc=%s user=%s",
                document_id,
                user_id,
            )
            return "skipped"

        # 4. Fetch document_type for display_name
        type_code = document.get("type_code") or document.get("type")
        doc_type = None
        if type_code:
            try:
                doc_type = await self.registry_gateway.get_document_type(type_code)
            except Exception:
                doc_type = None

        # 5. Load message template
        async with self.session_factory() as session:
            tpl_q = await session.execute(
                select(MessageTemplate).where(
                    MessageTemplate.channel == "email",
                    MessageTemplate.template_code == template_code,
                    MessageTemplate.locale == "ru",
                )
            )
            template = tpl_q.scalar_one_or_none()
        if template is None:
            log.warning(
                "send_document_reminder.template_not_found: tpl=%s", template_code
            )
            return "failed"

        # 6. Build variables for rendering
        expiry_iso = document.get("expiry_date") or ""
        days_left = 0
        days_overdue = 0
        if expiry_iso:
            try:
                exp = date.fromisoformat(expiry_iso[:10])
                delta = (exp - date.today()).days
                days_left = max(delta, 0)
                days_overdue = max(-delta, 0)
            except ValueError:
                pass

        expiry_human = format_date_ru(expiry_iso)
        variables: dict[str, Any] = {
            "full_name": user.get("full_name") or user.get("email"),
            "document_number": document.get("number") or "—",
            "document_type": (doc_type or {}).get("display_name", type_code or "—"),
            "asset_name": document.get("asset_name") or "—",
            "expiry_date": expiry_iso,
            # Human-readable extras (used by the redesigned templates).
            "expiry_human": expiry_human or expiry_iso or "—",
            "days_left": days_left,
            "days_overdue": days_overdue,
            "days_left_phrase": days_phrase(days_left),
            "days_overdue_phrase": days_phrase(days_overdue),
            "responsible_name": user.get("full_name") or "—",
            "document_url": f"{self.web_bff_base_url}/registry?document_id={document_id}",
        }

        # 7. Render subject + body
        try:
            subject = render_template(template.subject or "Лоцман", variables)
            body_md = render_template(template.body_md, variables)
        except TemplateRenderError as exc:
            log.warning("send_document_reminder.render_failed: %s", exc)
            await self._record_failed(
                document_id=document_id,
                user_id=user_id,
                template_code=template_code,
                scheduled_date=scheduled_date,
                error=f"render_error: {exc}",
            )
            return "failed"

        # 7a. Build the structured, branded HTML email: status accent + an
        #     at-a-glance details block + a single CTA. Plain text mirrors it.
        status_map = {"pre_notice": "soon", "in_day": "today", "overdue": "overdue"}
        status = status_map.get(template_code, "info")
        if template_code == "overdue":
            headline = f"Документ просрочен на {variables['days_overdue_phrase']}"
            remaining_label, remaining_value = "Просрочено", variables["days_overdue_phrase"]
        elif template_code == "in_day":
            headline = "Срок актуализации — сегодня"
            remaining_label, remaining_value = "Статус", "истекает сегодня"
        else:
            headline = f"Срок актуализации через {variables['days_left_phrase']}"
            remaining_label, remaining_value = "Осталось", variables["days_left_phrase"]

        details = [
            ("Компания", variables["asset_name"]),
            ("Тип документа", variables["document_type"]),
            ("№ документа", variables["document_number"]),
            ("Срок действия", variables["expiry_human"]),
            (remaining_label, remaining_value),
            ("Ответственный", variables["responsible_name"]),
        ]
        settings_url = f"{self.web_bff_base_url}/profile"
        body_html = render_notification_email(
            subject=subject,
            headline=headline,
            intro_html=render_markdown_subset(body_md),
            details=details,
            cta_url=variables["document_url"],
            settings_url=settings_url,
            status=status,
        )
        # Plain-text parity: headline + intro + key facts + link.
        detail_lines = "\n".join(
            f"{label}: {value}" for label, value in details if value and value != "—"
        )
        body_text = (
            f"{headline}\n\n{body_md.replace('**', '')}\n\n{detail_lines}\n\n"
            f"Открыть документ:\n{variables['document_url']}\n\n"
            f"Настроить уведомления: {settings_url}\n\n— Лоцман"
        )
        subject = subject.replace("**", "")

        # 8. Insert delivery_attempts row (pending) — must come before send to
        #    record the attempt even if the send crashes.
        attempt_id = await self._record_pending(
            document_id=document_id,
            user_id=user_id,
            template_code=template_code,
            scheduled_date=scheduled_date,
        )

        # 9. Send via SMTP/EWS
        send_ok, err = await self._send(
            to=str(user["email"]),
            subject=subject,
            body_text=body_text,
            body_html=body_html,
        )

        # 10. Finalise attempt row
        await self._finalise(
            attempt_id=attempt_id, ok=send_ok, error=err,
        )

        if send_ok:
            log.info(
                "send_document_reminder.sent: doc=%s user=%s tpl=%s",
                document_id,
                user_id,
                template_code,
            )
            return "sent"
        log.warning(
            "send_document_reminder.failed: doc=%s user=%s tpl=%s err=%s",
            document_id,
            user_id,
            template_code,
            err,
        )
        return "failed"

    # ── DB helpers ───────────────────────────────────────────────────────────

    async def _record_pending(
        self,
        *,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        template_code: str,
        scheduled_date: date,
    ) -> uuid.UUID:
        async with self.session_factory() as session, session.begin():
            row = DeliveryAttempt(
                document_id=document_id,
                user_id=user_id,
                channel="email",
                template_code=template_code,
                scheduled_at=datetime.combine(scheduled_date, datetime.min.time()).replace(
                    tzinfo=UTC
                ),
                status="pending",
            )
            session.add(row)
            await session.flush()
            return row.id

    async def _record_failed(
        self,
        *,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        template_code: str,
        scheduled_date: date,
        error: str,
    ) -> None:
        async with self.session_factory() as session, session.begin():
            row = DeliveryAttempt(
                document_id=document_id,
                user_id=user_id,
                channel="email",
                template_code=template_code,
                scheduled_at=datetime.combine(scheduled_date, datetime.min.time()).replace(
                    tzinfo=UTC
                ),
                status="failed",
                error=error[:1000],  # truncate to fit Text column safely
            )
            session.add(row)

    async def _finalise(
        self, *, attempt_id: uuid.UUID, ok: bool, error: str | None
    ) -> None:
        async with self.session_factory() as session, session.begin():
            row = await session.get(DeliveryAttempt, attempt_id)
            if row is None:
                return
            if ok:
                row.status = "sent"
                row.sent_at = datetime.now(tz=UTC)
            else:
                row.status = "failed"
                row.error = (error or "unknown")[:1000]
                row.retry_count = (row.retry_count or 0) + 1

    # ── Send (reuses _send_transactional_smart from internal_email) ──────────

    async def _send(
        self,
        *,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> tuple[bool, str | None]:
        import time

        # Load channel configs (email + exchange_calendar for EWS-fallback)
        async with self.session_factory() as session, session.begin():
            cred_repo = SqlaCredentialRepository(session)
            rows = await cred_repo.get_all()

        email_row = next(
            (r for r in rows if r.channel == "email" and r.enabled and r.config_enc),
            None,
        )
        if email_row is None:
            return False, "email_channel_not_configured"

        try:
            smtp_config = _cipher.decrypt(email_row.config_enc)
        except Exception as exc:
            return False, f"decrypt_failed: {type(exc).__name__}"

        ews_config: dict[str, Any] | None = None
        ews_row = next(
            (r for r in rows if r.channel == "exchange_calendar" and r.config_enc),
            None,
        )
        if ews_row is not None:
            try:
                ews_config = _cipher.decrypt(ews_row.config_enc)
            except Exception:
                ews_config = None

        t0 = time.perf_counter()
        try:
            await _send_transactional_smart(
                smtp_config=smtp_config,
                ews_config=ews_config,
                to=to,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
            )
            EMAIL_SEND_DURATION.labels(channel="email", outcome="success").observe(
                time.perf_counter() - t0
            )
            return True, None
        except Exception as exc:
            EMAIL_SEND_DURATION.labels(channel="email", outcome="failure").observe(
                time.perf_counter() - t0
            )
            return False, f"{type(exc).__name__}: {str(exc)[:200]}"
