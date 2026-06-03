# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Internal transactional email endpoint — /api/v1/internal/email/send

This endpoint is called by auth-service (and future internal callers) to send
a transactional email without the caller needing to know SMTP credentials.

Auth: internal JWT with aud='notification-service'.

POST /api/v1/internal/email/send
  Body: {to: str, subject: str, body_text: str}
  Returns: 200 {"queued": true}
  Errors:
    401  — missing / invalid internal JWT
    503  — email channel not configured or not enabled
    502  — SMTP / provider error

Design notes:
  - Reuses the same _send_email helper from test_channel.py to avoid
    duplicating aiosmtplib integration code.
  - Does NOT emit an outbox event — transactional emails (email change codes)
    are low-volume and single-use; the auth-service emits the audit event itself.
  - Role requirement is loosened to any authenticated internal caller (not just
    'admin') since auth-service uses a 'system' role on its internal JWTs.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from lotsman_shared.internal_jwt import InternalJWTClaims
from pydantic import BaseModel

from notification_service.api.deps import AppSettings, DbSession, current_actor
from notification_service.api.v1.admin_channels import _cipher
from notification_service.infrastructure.db.repositories import SqlaCredentialRepository
from notification_service.infrastructure.email_html import (
    render_markdown_subset,
    wrap_branded,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


class SendEmailRequest(BaseModel):
    to: str
    subject: str
    body_text: str


class SelfTestEmailRequest(BaseModel):
    """Request payload for `POST /internal/email/test-self`.

    Used by web-bff to send a diagnostic email to the currently-authenticated
    user. Notification-svc owns the message template + HTML rendering so SPA /
    BFF never see the body content directly.
    """

    recipient: str
    full_name: str | None = None
    ip_address: str | None = None
    initiated_at: str | None = None  # ISO-8601 from BFF; falls back to server time


def _require_any_actor(
    actor: Annotated[InternalJWTClaims | None, Depends(current_actor)],
) -> InternalJWTClaims:
    """Require a valid internal JWT (any role: admin, system, etc.)."""
    if actor is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return actor


@router.post("/email/send", status_code=200)
async def send_transactional_email(
    body: SendEmailRequest,
    db: DbSession,
    actor: Annotated[InternalJWTClaims, Depends(_require_any_actor)],
) -> dict[str, Any]:
    """Send a transactional email using the configured email channel.

    Returns: {"queued": true}
    503 if no email channel is configured or enabled.
    502 on SMTP / provider error.
    """
    # Load email channel config
    async with db.begin():
        repo = SqlaCredentialRepository(db)
        rows = await repo.get_all()

    email_row = next((r for r in rows if r.channel == "email"), None)

    if email_row is None or not email_row.config_enc:
        log.warning(
            "transactional_email.channel_not_configured",
            to=body.to,
            actor_id=str(actor.actor_id),
        )
        raise HTTPException(
            status_code=503,
            detail={
                "detail": "Email channel is not configured",
                "code": "EMAIL_CHANNEL_REQUIRED",
            },
        )

    if not email_row.enabled:
        log.warning(
            "transactional_email.channel_disabled",
            to=body.to,
            actor_id=str(actor.actor_id),
        )
        raise HTTPException(
            status_code=503,
            detail={
                "detail": "Email channel is disabled",
                "code": "EMAIL_CHANNEL_REQUIRED",
            },
        )

    try:
        config = _cipher.decrypt(email_row.config_enc)
    except Exception as exc:
        log.error("transactional_email.decrypt_failed", error=type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail={"detail": "Cannot decrypt email channel config", "code": "CHANNEL_DECRYPT_ERROR"},
        ) from exc

    # EWS-fallback: if the email channel SMTP send fails with a corp-Exchange
    # policy error (5.7.60 Send-As denied / 5.7.1 relay denied), retry via EWS
    # using the exchange_calendar channel's credentials. Same logic as in
    # test_channel.py — keeps both transports aware of the same fallback.
    ews_config: dict[str, Any] | None = None
    ews_row = next((r for r in rows if r.channel == "exchange_calendar"), None)
    if ews_row is not None and ews_row.config_enc:
        try:
            ews_config = _cipher.decrypt(ews_row.config_enc)
        except Exception:
            ews_config = None

    try:
        await _send_transactional_smart(
            smtp_config=config,
            ews_config=ews_config,
            to=body.to,
            subject=body.subject,
            body_text=body.body_text,
        )
    except Exception as exc:
        log.warning(
            "transactional_email.send_failed",
            to=body.to,
            error_class=type(exc).__name__,
            actor_id=str(actor.actor_id),
        )
        raise HTTPException(
            status_code=502,
            detail={"detail": "Failed to send email", "code": "SMTP_ERROR"},
        ) from exc

    log.info(
        "transactional_email.sent",
        to=body.to,
        subject=body.subject,
        actor_id=str(actor.actor_id),
    )
    return {"queued": True}


async def _load_email_configs(
    db: DbSession,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Load decrypted SMTP + optional EWS configs. Raises 503/502 on failure."""
    async with db.begin():
        repo = SqlaCredentialRepository(db)
        rows = await repo.get_all()

    email_row = next((r for r in rows if r.channel == "email"), None)
    if email_row is None or not email_row.config_enc:
        raise HTTPException(
            status_code=503,
            detail={
                "detail": "Email channel is not configured",
                "code": "EMAIL_CHANNEL_REQUIRED",
            },
        )
    if not email_row.enabled:
        raise HTTPException(
            status_code=503,
            detail={
                "detail": "Email channel is disabled",
                "code": "EMAIL_CHANNEL_REQUIRED",
            },
        )

    try:
        smtp_config = _cipher.decrypt(email_row.config_enc)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "detail": "Cannot decrypt email channel config",
                "code": "CHANNEL_DECRYPT_ERROR",
            },
        ) from exc

    ews_config: dict[str, Any] | None = None
    ews_row = next((r for r in rows if r.channel == "exchange_calendar"), None)
    if ews_row is not None and ews_row.config_enc:
        try:
            ews_config = _cipher.decrypt(ews_row.config_enc)
        except Exception:
            ews_config = None
    return smtp_config, ews_config


@router.post("/email/test-self", status_code=200)
async def send_self_test_email(
    body: SelfTestEmailRequest,
    db: DbSession,
    settings: AppSettings,
    actor: Annotated[InternalJWTClaims, Depends(_require_any_actor)],
) -> dict[str, Any]:
    """Send a diagnostic email to the user's own address.

    The web-bff route `POST /api/v1/me/test-email` calls this with the
    authenticated user's email (resolved server-side from `/auth/me`, NOT
    SPA-supplied). The body is a brand-wrapped HTML message stating delivery
    succeeded; recipient receives it as proof their notification channel works.
    """
    smtp_cfg, ews_cfg = await _load_email_configs(db)

    from datetime import UTC, datetime
    now = body.initiated_at or datetime.now(tz=UTC).isoformat(timespec="seconds")
    greeting = body.full_name or body.recipient

    subject = "Лоцман: проверка канала уведомлений"
    body_md = (
        f"Здравствуйте, **{greeting}**.\n\n"
        "Это тестовое сообщение от системы **Лоцман** — "
        "проверка доступности канала email-уведомлений.\n\n"
        "Если вы видите это письмо — рассылка работает: "
        "напоминания о сроках актуализации документов будут приходить "
        "на этот почтовый ящик.\n\n"
        f"Время отправки: **{now}** UTC.\n"
        f"Получатель: **{body.recipient}**.\n\n"
        f"Открыть систему:\n{settings.web_bff_url.rstrip('/')}/registry"
    )
    body_html = wrap_branded(
        subject=subject,
        body_html=render_markdown_subset(body_md),
        footer_note=(
            "Это автоматическое сообщение системы «Лоцман». "
            "Отправлено по вашему запросу из профиля пользователя."
        ),
    )
    body_text = body_md.replace("**", "")

    try:
        await _send_transactional_smart(
            smtp_config=smtp_cfg,
            ews_config=ews_cfg,
            to=body.recipient,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
        )
    except Exception as exc:
        log.warning(
            "self_test_email.send_failed",
            to=body.recipient,
            error_class=type(exc).__name__,
            actor_id=str(actor.actor_id),
        )
        raise HTTPException(
            status_code=502,
            detail={"detail": "Failed to send test email", "code": "SMTP_ERROR"},
        ) from exc

    log.info(
        "self_test_email.sent",
        to=body.recipient,
        actor_id=str(actor.actor_id),
    )
    return {"sent": True}


async def _send_transactional(
    *,
    config: dict[str, Any],
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> None:
    """Send a transactional email via aiosmtplib (same logic as test_channel).

    When body_html is provided, sends a multipart/alternative message containing
    both plain-text (body_text) and HTML (body_html) parts. Clients that support
    HTML render the HTML part; legacy/text-only clients fall back to body_text.
    """
    try:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        import aiosmtplib
    except ImportError as exc:
        raise RuntimeError(
            "aiosmtplib is required for email channel. "
            "Add it to notification-service dependencies."
        ) from exc

    from_address = str(config["from_address"])
    from_name = str(config.get("from_name") or "")

    msg: MIMEText | MIMEMultipart
    if body_html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))
    else:
        msg = MIMEText(body_text, "plain", "utf-8")

    msg["Subject"] = subject
    msg["From"] = from_address
    if from_name:
        msg["Reply-To"] = f"{from_name} <{from_address}>"
    msg["To"] = to
    for hk, hv in (extra_headers or {}).items():
        msg[hk] = hv

    port = int(config["smtp_port"])
    use_tls = port == 465
    start_tls = port == 587

    smtp_user = str(config.get("smtp_user") or "").strip()
    smtp_pass = str(config.get("smtp_password") or "")
    auth_kwargs: dict[str, Any] = {}
    if smtp_user:
        auth_kwargs["username"] = smtp_user
        auth_kwargs["password"] = smtp_pass

    await aiosmtplib.send(
        msg,
        hostname=str(config["smtp_host"]),
        port=port,
        use_tls=use_tls,
        start_tls=start_tls,
        sender=from_address,
        recipients=[to],
        **auth_kwargs,
    )


# ---------------------------------------------------------------------------
# EWS-fallback wrapper (mirrors logic in test_channel.py)
# ---------------------------------------------------------------------------


async def _send_transactional_smart(
    *,
    smtp_config: dict[str, Any],
    ews_config: dict[str, Any] | None,
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> None:
    """Try SMTP first; on Exchange policy block, fall back to EWS.

    Mirrors `_send_email_smart` from test_channel.py — but with custom subject/body
    instead of hardcoded test message. Imports the EWS helper to avoid duplication.

    When body_html is provided, both transports send the HTML body alongside the
    plain-text fallback.
    """
    try:
        await _send_transactional(
            config=smtp_config,
            to=to,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            extra_headers=extra_headers,
        )
        return
    except Exception as smtp_exc:
        if ews_config is None:
            raise

        # Reuse the policy-error detector + EWS sender from test_channel
        from notification_service.application.use_cases.test_channel import (
            _looks_like_smtp_policy_error,
            _send_via_ews_sync,
        )

        if not _looks_like_smtp_policy_error(smtp_exc):
            raise
        log.info(
            "transactional_email.smtp_blocked_falling_back_to_ews",
            smtp_error_class=type(smtp_exc).__name__,
        )
        try:
            import asyncio as _asyncio
            await _asyncio.to_thread(
                _send_via_ews_sync,
                ews_config=ews_config,
                recipient=to,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
            )
            log.info("transactional_email.via_ews_succeeded")
        except Exception as ews_exc:
            log.warning(
                "transactional_email.ews_fallback_failed",
                ews_error_class=type(ews_exc).__name__,
            )
            raise ews_exc from smtp_exc
