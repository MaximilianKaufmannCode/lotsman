# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""TestChannel use case — US-5.

Sends a synthetic test notification to verify channel connectivity.

Scope note:
  - Email is implemented via aiosmtplib.
  - Telegram and Dion return 501 NOT_IMPLEMENTED. Wiring those providers
    is deferred to the notifications feature (Phase 4). This is an explicit
    scope reduction documented in the Phase 2b implementation report.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from notification_service.application.ports import CredentialRepository, EventOutbox
from notification_service.domain.channels import Channel
from notification_service.domain.errors import (
    ChannelDecryptError,
    ChannelNotConfiguredError,
    ChannelNotImplementedError,
)
from notification_service.domain.events import ChannelTested
from notification_service.infrastructure.channel_crypto import ChannelCipher

log = structlog.get_logger(__name__)

_TEST_SUBJECT = "Лоцман — тестовое уведомление"
_TEST_BODY = (
    "Это тестовое сообщение от системы Лоцман.\n"
    "Если вы его получили, канал email настроен корректно."
)


@dataclass(slots=True)
class TestChannel:
    """Send a synthetic test message to verify channel credentials."""

    credential_repo: CredentialRepository
    outbox: EventOutbox
    cipher: ChannelCipher

    async def execute(
        self,
        *,
        actor_id: uuid.UUID,
        channel: Channel,
        recipient: str,
    ) -> dict[str, object]:
        """Returns ``{queued: True, destination: str, test_id: UUID}`` on success.

        Raises:
            ChannelNotConfiguredError: no row for this channel.
            ChannelDecryptError: row exists but decryption fails.
            ChannelNotImplementedError: telegram/dion not wired yet.
        """
        # Only email is implemented in Phase 2b.
        if channel != "email":
            raise ChannelNotImplementedError(
                f"Test endpoint for channel '{channel}' is not yet implemented. "
                "Telegram and Dion send will be wired in the notifications feature (Phase 4)."
            )

        # Load credential row.
        rows = await self.credential_repo.get_all()
        row = next((r for r in rows if r.channel == channel), None)
        if row is None or not row.config_enc:
            raise ChannelNotConfiguredError()

        # Decrypt config.
        try:
            config = self.cipher.decrypt(row.config_enc)
        except Exception as exc:
            raise ChannelDecryptError() from exc

        test_id = uuid.uuid4()

        # If recipient is empty (BFF didn't pass admin email for some reason) —
        # fallback to from_address so the test still validates send capability.
        # No more domain-based silent re-write: the SPA / BFF passes the
        # admin's current email and we honor it. If Exchange can't deliver
        # there (e.g. external domain blocked) the SMTP server will reply
        # with a meaningful error that's surfaced via smtp_reply.
        if not recipient:
            from_addr = str(config.get("from_address") or "")
            if from_addr:
                recipient = from_addr

        # Look up exchange_calendar config (if any) — used as EWS fallback for
        # email send when SMTP fails with «Send-As denied» / «relay denied»
        # (very common with corp Exchange where IT didn't grant SMTP Send-As
        # to the service account but EWS-based send works because it goes
        # through the calendar mailbox the account already owns).
        ews_config: dict[str, Any] | None = None
        ews_row = next((r for r in rows if r.channel == "exchange_calendar"), None)
        if ews_row is not None and ews_row.config_enc:
            try:
                ews_config = self.cipher.decrypt(ews_row.config_enc)
            except Exception:
                ews_config = None

        transport: str | None = None
        try:
            transport = await _send_email_smart(
                smtp_config=config,
                ews_config=ews_config,
                recipient=recipient,
            )
            outcome = "queued"
            error_class = None
        except Exception as exc:
            error_class = type(exc).__name__
            outcome = "failed"
            # Log at WARNING without including credential values.
            # Include only the SMTP server's reply (which may contain the
            # explanation like "550 5.7.60 SMTP relay not allowed") so that
            # admin can fix configuration. SMTP replies do NOT echo the
            # password.
            smtp_reply: str | None = None
            for attr in ("smtp_message", "message", "args"):
                v = getattr(exc, attr, None)
                if v:
                    smtp_reply = str(v)[:300]
                    break
            log.warning(
                "channel_test_failed",
                channel=channel,
                error_class=error_class,
                smtp_reply=smtp_reply,
                actor_id=str(actor_id),
            )

        await self.outbox.publish(
            ChannelTested(
                actor_id=actor_id,
                channel=channel,
                outcome=outcome,
                destination=recipient,
                test_id=test_id,
                error_class=error_class,
            ).as_envelope()
        )

        if outcome == "failed":
            # Re-raise a sanitised error (no credentials in message).
            from notification_service.domain.errors import ProviderError
            raise ProviderError(
                f"Не удалось доставить тестовое сообщение через канал '{channel}'. "
                "Проверьте параметры подключения."
            )

        return {
            "queued": True,
            "destination": recipient,
            "test_id": test_id,
            "transport": transport,  # "smtp" or "ews" (None for non-email channels)
        }


async def _send_test_email(*, config: dict[str, Any], recipient: str) -> None:
    """Send a test email via aiosmtplib.

    Intentionally does NOT log config values (smtp_password etc.).
    """
    try:
        from email.mime.text import MIMEText

        import aiosmtplib
    except ImportError as exc:
        raise RuntimeError(
            "aiosmtplib is required for email channel testing. "
            "Add it to notification-service dependencies."
        ) from exc

    from_address = str(config["from_address"])
    from_name = str(config.get("from_name") or "")
    msg = MIMEText(_TEST_BODY, "plain", "utf-8")
    msg["Subject"] = _TEST_SUBJECT
    # Use a plain `From: addr` (no display-name) by default — some Exchange
    # policies enforce strict "Send As" matching against the bare RFC-5322
    # address and reject display-name forms. If the operator wants the
    # display-name to appear in Outlook they can set from_name; we still emit
    # it but as Reply-To which Exchange does not gate.
    msg["From"] = from_address
    if from_name:
        msg["Reply-To"] = f"{from_name} <{from_address}>"
    msg["To"] = recipient

    # TLS auto-detect by port (covers ~95% of corporate setups):
    # - 465 → implicit TLS from connect (use_tls=True)
    # - 587 → submission with STARTTLS upgrade (start_tls=True; required by
    #   Exchange/postfix before AUTH)
    # - 25  → plain (rarely supports AUTH; leave both False)
    port = int(config["smtp_port"])
    use_tls = port == 465
    start_tls = port == 587

    # If smtp_user/password are empty — anonymous submission (typical for
    # internal port 25 relay where the server trusts internal IPs without AUTH).
    # aiosmtplib tries AUTH if username is set even to "" — explicitly omit.
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
        # Explicit envelope sender — Exchange "Send As" check uses this, not
        # the MIME From header. Match it to the authenticated user's primary
        # SMTP (= from_address) so the check passes when service-account is
        # the rightful owner of the mailbox.
        sender=from_address,
        recipients=[recipient],
        **auth_kwargs,
    )


# ---------------------------------------------------------------------------
# EWS-based send (corp-Exchange friendly fallback)
# ---------------------------------------------------------------------------
#
# When SMTP fails with «Send-As denied» or relay-denied, we can usually still
# send the email via EWS — the same path the calendar channel uses. The
# service account must own (or have full-access to) the target mailbox; that's
# already true if exchange_calendar channel is configured and tested OK.

# Errors that mean «SMTP path is policy-blocked, try EWS»: Exchange returns
# 5.7.x for these.
_EWS_FALLBACK_TRIGGER_TOKENS = (
    "5.7.60",  # Send-As denied
    "5.7.1",   # Relay denied / blocked due to security
    "5.7.3",   # Auth unsuccessful (could try EWS auth instead)
    "550 5.7", # generic 5.7 family
    "STARTTLS not supported",  # legacy port 25 with auth attempt
)


def _looks_like_smtp_policy_error(exc: BaseException) -> bool:
    """Return True if the exception suggests SMTP policy block (try EWS)."""
    msg_parts: list[str] = []
    for attr in ("smtp_message", "message", "args"):
        v = getattr(exc, attr, None)
        if v:
            msg_parts.append(str(v))
    blob = " | ".join(msg_parts).lower()
    return any(tok.lower() in blob for tok in _EWS_FALLBACK_TRIGGER_TOKENS)


def _send_via_ews_sync(
    *,
    ews_config: dict[str, Any],
    recipient: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> None:
    """Send an email via Exchange EWS using exchangelib.

    Uses the same auth + mailbox as the exchange_calendar channel:
    `service_account_login` + `service_account_password` against `target_mailbox`
    on `ews_url`. Sends as the target_mailbox (which the service account owns
    via DELEGATE/IMPERSONATION — the same right the calendar test already proved).

    When body_html is provided, the message body is sent as HTML (exchangelib's
    HTMLBody); otherwise plain text. Outlook renders HTMLBody correctly; there
    is no native multipart/alternative knob in exchangelib, so we send a single
    HTML part — Outlook itself extracts plain-text for indexing.

    NOTE: synchronous (exchangelib has no native async); call from
    `asyncio.to_thread()`.
    """
    from exchangelib import (
        BASIC,
        DELEGATE,
        NTLM,
        Account,
        Configuration,
        Credentials,
        HTMLBody,
        Mailbox,
        Message,
    )
    from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter

    if not ews_config.get("verify_ssl", True):
        BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter

    auth_type = NTLM if str(ews_config.get("auth_type", "NTLM")).upper() == "NTLM" else BASIC
    creds = Credentials(
        username=str(ews_config["service_account_login"]),
        password=str(ews_config["service_account_password"]),
    )
    cfg = Configuration(
        service_endpoint=str(ews_config["ews_url"]),
        credentials=creds,
        auth_type=auth_type,
    )
    target_mailbox = str(ews_config["target_mailbox"])
    account = Account(
        primary_smtp_address=target_mailbox,
        config=cfg,
        autodiscover=False,
        access_type=DELEGATE,
    )
    msg = Message(
        account=account,
        subject=subject,
        body=HTMLBody(body_html) if body_html else body_text,
        to_recipients=[Mailbox(email_address=recipient)],
    )
    msg.send()


async def _send_via_ews(*, ews_config: dict[str, Any], recipient: str) -> None:
    """Async wrapper — exchangelib runs sync, offload to thread."""
    import asyncio
    await asyncio.to_thread(
        _send_via_ews_sync,
        ews_config=ews_config,
        recipient=recipient,
        subject=_TEST_SUBJECT,
        body_text=_TEST_BODY,
    )


async def _send_email_smart(
    *,
    smtp_config: dict[str, Any],
    ews_config: dict[str, Any] | None,
    recipient: str,
) -> str:
    """Try SMTP first; on Exchange policy block, fall back to EWS-based send.

    Returns the transport name that succeeded — "smtp" or "ews" — so the
    caller can surface this to the operator (e.g. «Доставлено через
    корпоративный Exchange» vs «Доставлено через настроенный SMTP»).
    """
    try:
        await _send_test_email(config=smtp_config, recipient=recipient)
        return "smtp"
    except Exception as smtp_exc:
        if ews_config is None:
            raise  # no EWS configured — surface the SMTP error
        if not _looks_like_smtp_policy_error(smtp_exc):
            raise  # connectivity / DNS / auth bug — fixing won't help
        log.info(
            "email_send.smtp_blocked_falling_back_to_ews",
            smtp_error_class=type(smtp_exc).__name__,
        )
        try:
            await _send_via_ews(ews_config=ews_config, recipient=recipient)
            log.info("email_send.via_ews_succeeded")
            return "ews"
        except Exception as ews_exc:
            # Re-raise EWS error — it's the more meaningful one for diagnosis
            log.warning(
                "email_send.ews_fallback_failed",
                ews_error_class=type(ews_exc).__name__,
            )
            raise ews_exc from smtp_exc
