# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""HTTP adapter that sends transactional emails via notification-service.

Implements the TransactionalEmailSender port (application/ports.py).

Calls: POST /api/v1/internal/email/send on notification-service.
Auth:  Internal JWT addressed to notification-service.

On 503 from notification-service → raises EmailChannelNotConfiguredError.
On any other non-2xx → re-raises as a generic Exception (caller maps to 502).
"""

from __future__ import annotations

import uuid

import httpx
import structlog
from lotsman_shared.internal_jwt import issue_internal_jwt

from auth_service.domain.errors import EmailChannelNotConfiguredError

log = structlog.get_logger(__name__)

_TIMEOUT_SECONDS = 10.0


class NotificationServiceEmailAdapter:
    """Implements TransactionalEmailSender by calling notification-service.

    Args:
        notification_svc_url: Base URL of notification-service.
        signing_key: HS256 key for internal JWTs addressed to notification-service.
        system_actor_id: UUID to use as the actor in the internal JWT sub claim.
        ttl_seconds: JWT TTL in seconds.
    """

    def __init__(
        self,
        notification_svc_url: str,
        signing_key: str,
        system_actor_id: uuid.UUID,
        ttl_seconds: int = 60,
    ) -> None:
        self._base_url = notification_svc_url.rstrip("/")
        self._signing_key = signing_key
        self._actor_id = system_actor_id
        self._ttl = ttl_seconds

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body_text: str,
    ) -> None:
        """POST /api/v1/internal/email/send on notification-service.

        Raises:
            EmailChannelNotConfiguredError: on 503 (channel not configured).
            RuntimeError: on any other non-2xx status.
        """
        token = issue_internal_jwt(
            self._signing_key,
            actor_id=self._actor_id,
            role="system",
            audience="notification-service",
            ttl_seconds=self._ttl,
        )
        headers = {"X-Internal-Token": token}
        payload = {"to": to, "subject": subject, "body_text": body_text}

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    f"{self._base_url}/api/v1/internal/email/send",
                    headers=headers,
                    json=payload,
                )
        except Exception as exc:
            log.warning(
                "transactional_email.network_error",
                to=to,
                error=type(exc).__name__,
            )
            raise RuntimeError(
                f"Failed to reach notification-service for transactional email: {exc}"
            ) from exc

        if resp.status_code == 503:
            log.warning("transactional_email.channel_not_configured", to=to)
            raise EmailChannelNotConfiguredError()

        if not resp.is_success:
            # 502 = provider returned error (e.g. Exchange «5.7.60 Send-As denied»),
            # 500 = upstream crash, etc. From the user's perspective the result
            # is the same: «email channel can't deliver right now — admin must
            # fix it». Surface as EmailChannelNotConfiguredError so the SPA
            # shows the typed «обратитесь к администратору» banner instead of
            # an unhandled 500.
            log.warning(
                "transactional_email.upstream_error",
                to=to,
                status=resp.status_code,
                body=resp.text[:200],
            )
            raise EmailChannelNotConfiguredError()

        log.info("transactional_email.sent", to=to, subject=subject)
