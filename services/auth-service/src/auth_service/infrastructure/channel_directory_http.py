# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""HTTP adapter for ChannelDirectoryReader port.

Calls notification-service GET /api/v1/admin/channels and returns
the list of enabled channel names.

Implements the ChannelDirectoryReader Protocol from application/ports.py.
"""

from __future__ import annotations

import uuid

import httpx
import structlog
from lotsman_shared.internal_jwt import issue_internal_jwt

log = structlog.get_logger(__name__)

_CHANNEL_PRIORITY = ["email", "telegram", "dion"]


class ChannelDirectoryHttpAdapter:
    """Calls notification-service to check which channels are enabled.

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

    async def get_enabled_channels(self) -> list[str]:
        """Return enabled channel names in priority order (email > telegram > dion).

        On HTTP error or timeout → returns empty list (caller treats as no channel).
        This is defensive: invite_user raises NoEnabledChannelError when the list
        is empty, which is the correct behavior.
        """
        token = issue_internal_jwt(
            self._signing_key,
            actor_id=self._actor_id,
            role="admin",
            audience="notification-service",
            ttl_seconds=self._ttl,
        )
        headers = {"X-Internal-Token": token}

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self._base_url}/api/v1/admin/channels",
                    headers=headers,
                )
            resp.raise_for_status()
            data: list[dict[str, object]] = resp.json()
            enabled = [row["channel"] for row in data if row.get("enabled")]
            # Sort by priority.
            return [ch for ch in _CHANNEL_PRIORITY if ch in enabled]
        except Exception as exc:
            log.warning(
                "channel_directory_lookup_failed",
                error=type(exc).__name__,
                detail=str(exc),
            )
            return []
