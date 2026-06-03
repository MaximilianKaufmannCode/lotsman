# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redis Stream consumer for auth.invite events.

Subscribes to the `auth.invite` stream produced by auth-service's outbox
dispatcher (see services/auth-service/.../outbox/dispatcher.py).
Consumer group: notification-invite-dispatcher.

Handled event types:
  - notification.invite.requested.v1

Side effect for each event:
  1. Read OTP from Redis key `invite:otp:{invitation_id}` (set by auth-svc).
  2. Render an invite email (subject + body with OTP + login_url).
  3. Send via the configured channel (currently: email; telegram/dion TBD).
  4. DELETE the OTP key on success (one-shot delivery).
  5. XACK the stream message.

Delivery semantics: at-least-once; if email send fails the message stays in
the PEL and is retried on next loop iteration. The OTP key TTL is 600s
(set by auth-svc), so retries beyond that window will fail with «OTP not
found» — that is the expected upper bound for invite delivery latency.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import redis.asyncio as aioredis
import structlog

log = structlog.get_logger(__name__)

_STREAM_KEY = "auth.invite"
_CONSUMER_GROUP = "notification-invite-dispatcher"
_CONSUMER_NAME = "notification-svc-1"
_BLOCK_MS = 5_000
_BATCH_SIZE = 10

_SUBSCRIBED_TYPES = {
    "notification.invite.requested.v1",
}


class InviteConsumer:
    """Runs a Redis XREADGROUP loop and dispatches invite OTPs via email."""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        session_factory: Any,
        stream_key: str = _STREAM_KEY,
        web_bff_url: str = "",
    ) -> None:
        self._redis = redis_client
        self._session_factory = session_factory
        self._stream_key = stream_key
        # Single source of truth for the login link (env WEB_BFF_URL). Falls back
        # to the event payload / on-prem default only if not configured.
        self._web_bff_url = (web_bff_url or "").rstrip("/")
        self._running = False

    async def start(self) -> None:
        try:
            await self._redis.xgroup_create(
                self._stream_key,
                _CONSUMER_GROUP,
                id="0",  # also pick up events that were published before group creation
                mkstream=True,
            )
            log.info(
                "invite_consumer.group_created",
                stream=self._stream_key,
                group=_CONSUMER_GROUP,
            )
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
            log.debug("invite_consumer.group_already_exists", group=_CONSUMER_GROUP)

        self._running = True
        await self._run_loop()

    def stop(self) -> None:
        self._running = False

    async def _run_loop(self) -> None:
        log.info("invite_consumer.loop_started", stream=self._stream_key)
        while self._running:
            try:
                results = await self._redis.xreadgroup(
                    groupname=_CONSUMER_GROUP,
                    consumername=_CONSUMER_NAME,
                    streams={self._stream_key: ">"},
                    count=_BATCH_SIZE,
                    block=_BLOCK_MS,
                )
                if not results:
                    continue
                for _stream, messages in results:
                    for msg_id, fields in messages:
                        await self._handle_message(msg_id, fields)
            except asyncio.CancelledError:
                log.info("invite_consumer.cancelled")
                break
            except Exception:
                log.exception("invite_consumer.loop_error")
                await asyncio.sleep(2)

        log.info("invite_consumer.loop_stopped")

    async def _handle_message(
        self, msg_id: str | bytes, fields: dict[Any, Any]
    ) -> None:
        decoded: dict[str, str] = {
            (k.decode() if isinstance(k, bytes) else k): (
                v.decode() if isinstance(v, bytes) else v
            )
            for k, v in fields.items()
        }

        raw_type = decoded.get("type", '""')
        try:
            event_type = json.loads(raw_type)
        except (ValueError, TypeError):
            event_type = raw_type

        if event_type not in _SUBSCRIBED_TYPES:
            await self._xack(msg_id)
            return

        try:
            payload_raw = decoded.get("payload", "{}")
            payload: dict[str, Any] = json.loads(payload_raw)

            invitation_id = payload.get("invitation_id")
            email = payload.get("email")
            # Prefer the configured public URL (WEB_BFF_URL) over the event's
            # login_url so invite links always match the real serving domain.
            login_url = (
                self._web_bff_url
                or payload.get("login_url")
                or "https://lotsman.example.com"
            )
            channel_pref = payload.get("channel_preference", "email")
            ttl_minutes = int(payload.get("ttl_minutes") or 10)

            if not invitation_id or not email:
                log.warning(
                    "invite_consumer.payload_invalid",
                    msg_id=msg_id,
                    have_invitation_id=bool(invitation_id),
                    have_email=bool(email),
                )
                await self._xack(msg_id)
                return

            otp = await self._read_otp(invitation_id)
            if otp is None:
                log.warning(
                    "invite_consumer.otp_missing_or_expired",
                    invitation_id=invitation_id,
                    email=email,
                )
                await self._xack(msg_id)
                return

            if channel_pref != "email":
                log.warning(
                    "invite_consumer.channel_not_supported",
                    channel=channel_pref,
                    invitation_id=invitation_id,
                )
                await self._xack(msg_id)
                return

            sent = await self._send_invite_email(
                to=email,
                otp=otp,
                login_url=login_url,
                ttl_minutes=ttl_minutes,
            )
            if sent:
                await self._delete_otp(invitation_id)
                log.info(
                    "invite_consumer.dispatched",
                    invitation_id=invitation_id,
                    email=email,
                )
                await self._xack(msg_id)
            # else: leave in PEL — will retry on next loop iteration

        except Exception:
            log.exception(
                "invite_consumer.handle_failed",
                msg_id=msg_id,
                event_type=event_type,
            )
            return

    async def _read_otp(self, invitation_id: str) -> str | None:
        key = f"invite:otp:{invitation_id}"
        raw = await self._redis.get(key)
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    async def _delete_otp(self, invitation_id: str) -> None:
        try:
            await self._redis.delete(f"invite:otp:{invitation_id}")
        except Exception:
            log.warning("invite_consumer.otp_delete_failed", invitation_id=invitation_id)

    async def _send_invite_email(
        self,
        *,
        to: str,
        otp: str,
        login_url: str,
        ttl_minutes: int,
    ) -> bool:
        """Render and send the invite email. Returns True on success."""
        from notification_service.api.v1.admin_channels import _cipher
        from notification_service.api.v1.internal_email import (
            _send_transactional_smart,
        )
        from notification_service.infrastructure.db.repositories import (
            SqlaCredentialRepository,
        )

        async with self._session_factory() as session, session.begin():
            repo = SqlaCredentialRepository(session)
            rows = await repo.get_all()

        email_row = next(
            (r for r in rows if r.channel == "email" and r.enabled and r.config_enc),
            None,
        )
        if email_row is None:
            log.warning("invite_consumer.email_channel_unavailable", to=to)
            return False

        try:
            smtp_config = _cipher.decrypt(email_row.config_enc)
        except Exception:
            log.exception("invite_consumer.email_decrypt_failed")
            return False

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

        subject = "Лоцман — приглашение в систему"
        body_text = _render_invite_body(otp=otp, login_url=login_url, ttl_minutes=ttl_minutes)

        try:
            await _send_transactional_smart(
                smtp_config=smtp_config,
                ews_config=ews_config,
                to=to,
                subject=subject,
                body_text=body_text,
            )
        except Exception as exc:
            log.warning(
                "invite_consumer.email_send_failed",
                to=to,
                error_class=type(exc).__name__,
            )
            return False

        return True

    async def _xack(self, msg_id: str | bytes) -> None:
        try:
            await self._redis.xack(self._stream_key, _CONSUMER_GROUP, msg_id)
        except Exception:
            log.warning("invite_consumer.xack_failed", msg_id=msg_id)


def _render_invite_body(*, otp: str, login_url: str, ttl_minutes: int) -> str:
    return (
        "Здравствуйте!\n\n"
        "Вас пригласили в корпоративную систему «Лоцман» — "
        "реестр документов и уведомлений.\n\n"
        f"Ваш одноразовый код для первого входа: {otp}\n"
        f"Код действителен {ttl_minutes} минут.\n\n"
        f"Войти: {login_url}\n\n"
        "Используйте свой email и этот код. После первого входа система "
        "попросит задать постоянный пароль и подключить TOTP-приложение "
        "(Google Authenticator / 1Password / Yandex Key) для двухфакторной "
        "аутентификации.\n\n"
        "Если вы не ожидали это приглашение — просто проигнорируйте письмо.\n\n"
        "— Лоцман"
    )
