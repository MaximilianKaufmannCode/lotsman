# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Shared transactional email sender (SMTP + EWS fallback).

Extracted so event notifications (ADR-0011) and reminders share one send path.
Loads the encrypted email/exchange_calendar channel configs from
provider_credentials and delegates to `_send_transactional_smart`.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from notification_service.api.v1.admin_channels import _cipher
from notification_service.api.v1.internal_email import _send_transactional_smart
from notification_service.infrastructure.db.repositories import SqlaCredentialRepository

log = structlog.get_logger(__name__)


async def send_email(
    *,
    session_factory: async_sessionmaker[Any],
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> tuple[bool, str | None]:
    """Send one email via SMTP (EWS fallback). Returns (ok, error)."""
    async with session_factory() as session, session.begin():
        cred_repo = SqlaCredentialRepository(session)
        rows = await cred_repo.get_all()

    email_row = next(
        (r for r in rows if r.channel == "email" and r.enabled and r.config_enc), None
    )
    if email_row is None:
        return False, "email_channel_not_configured"

    try:
        smtp_config = _cipher.decrypt(email_row.config_enc)
    except Exception as exc:  # noqa: BLE001
        return False, f"decrypt_failed: {type(exc).__name__}"

    ews_config: dict[str, Any] | None = None
    ews_row = next(
        (r for r in rows if r.channel == "exchange_calendar" and r.config_enc), None
    )
    if ews_row is not None:
        try:
            ews_config = _cipher.decrypt(ews_row.config_enc)
        except Exception:  # noqa: BLE001
            ews_config = None

    try:
        await _send_transactional_smart(
            smtp_config=smtp_config,
            ews_config=ews_config,
            to=to,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
        )
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:200]}"
