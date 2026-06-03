# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Async HTTP client for notification-service."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from web_bff.infrastructure.clients.base import DownstreamClient


class NotificationClient(DownstreamClient):
    """Client for notification-service endpoints.

    Audience: 'notification-service'
    """

    AUDIENCE = "notification-service"

    # ------------------------------------------------------------------
    # Admin channel management (ADR-0004 Phase 2b)
    # ------------------------------------------------------------------

    async def admin_list_channels(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """GET /api/v1/admin/channels."""
        return await self.get(
            "/api/v1/admin/channels",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def admin_get_channel_config(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        channel: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """GET /api/v1/admin/channels/{channel}/config — config with secrets masked."""
        return await self.get(
            f"/api/v1/admin/channels/{channel}/config",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def admin_set_channel(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        channel: str,
        body: dict[str, Any],
        request_id: str | None = None,
    ) -> httpx.Response:
        """PUT /api/v1/admin/channels/{channel}."""
        return await self.put(
            f"/api/v1/admin/channels/{channel}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    async def admin_patch_channel(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        channel: str,
        body: dict[str, Any],
        request_id: str | None = None,
    ) -> httpx.Response:
        """PATCH /api/v1/admin/channels/{channel}."""
        return await self.patch(
            f"/api/v1/admin/channels/{channel}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    async def admin_test_channel(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        channel: str,
        body: dict[str, Any],
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/admin/channels/{channel}/test.

        Test calls hit external services (SMTP/Telegram/Dion) whose connect
        can take 10-30s on misconfigured hosts. Override the default 5s
        client timeout to 45s so we get a typed ProviderError back instead
        of a 500 from BFF-side httpx.ReadTimeout.
        """
        return await self.post(
            f"/api/v1/admin/channels/{channel}/test",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
            timeout=45.0,
        )

    async def admin_test_exchange_calendar(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/admin/channels/exchange_calendar/test — EWS probe.

        EWS round-trip across corp VPN can take 5-15s; allow 45s like
        admin_test_channel.
        """
        return await self.post(
            "/api/v1/admin/channels/exchange_calendar/test",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={},
            timeout=45.0,
        )

    # ------------------------------------------------------------------
    # Calendar subscription management (ADR-0005 §3)
    # ------------------------------------------------------------------

    async def admin_list_calendar_subscriptions(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """GET /api/v1/admin/calendar-subscriptions."""
        return await self.get(
            "/api/v1/admin/calendar-subscriptions",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def admin_add_calendar_subscription(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        body: dict[str, Any],
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/admin/calendar-subscriptions."""
        return await self.post(
            "/api/v1/admin/calendar-subscriptions",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    async def admin_remove_calendar_subscription(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        body: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> httpx.Response:
        """DELETE /api/v1/admin/calendar-subscriptions/{user_id}."""
        return await self.delete(
            f"/api/v1/admin/calendar-subscriptions/{user_id}",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body or {},
        )

    async def admin_retry_calendar_share(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        body: dict[str, Any],
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/admin/calendar-subscriptions/{user_id}/retry-share.

        EWS calls can be slow; allow 45s like channel tests.
        """
        return await self.post(
            f"/api/v1/admin/calendar-subscriptions/{user_id}/retry-share",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
            timeout=45.0,
        )

    async def admin_mark_calendar_share_granted(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/admin/calendar-subscriptions/{user_id}/mark-granted."""
        return await self.post(
            f"/api/v1/admin/calendar-subscriptions/{user_id}/mark-granted",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={},
        )

    async def admin_list_notifications_history(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        params: dict[str, Any],
        request_id: str | None = None,
    ) -> httpx.Response:
        """GET /api/v1/admin/notifications/history — paginated delivery history."""
        return await self.get(
            "/api/v1/admin/notifications/history",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            params=params,
        )

    async def send_self_test_email(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        recipient: str,
        full_name: str | None,
        ip_address: str | None,
        initiated_at: str | None,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/internal/email/test-self — diagnostic email to user's own inbox.

        Recipient is resolved server-side by the BFF (from `/auth/me`), never
        accepted from SPA — this prevents spam-by-redirect.
        """
        return await self.post(
            "/api/v1/internal/email/test-self",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={
                "recipient": recipient,
                "full_name": full_name,
                "ip_address": ip_address,
                "initiated_at": initiated_at,
            },
            timeout=45.0,
        )

    # ------------------------------------------------------------------
    # Per-user notification preferences (ADR-0011 §D2/§D3)
    # ------------------------------------------------------------------

    async def get_my_notification_prefs(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """GET /api/v1/me/notification-prefs — caller's effective prefs."""
        return await self.get(
            "/api/v1/me/notification-prefs",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def put_my_notification_prefs(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        body: dict[str, Any],
        request_id: str | None = None,
    ) -> httpx.Response:
        """PUT /api/v1/me/notification-prefs — upsert the caller's prefs."""
        return await self.put(
            "/api/v1/me/notification-prefs",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json=body,
        )

    # ------------------------------------------------------------------
    # In-app notification feed (ADR-0011 §D6)
    # ------------------------------------------------------------------

    async def list_my_notifications(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        limit: int = 30,
        offset: int = 0,
        request_id: str | None = None,
    ) -> httpx.Response:
        """GET /api/v1/me/notifications — feed + unread count."""
        return await self.get(
            "/api/v1/me/notifications",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            params={"limit": limit, "offset": offset},
        )

    async def my_unread_count(
        self, *, actor_id: uuid.UUID, role: str, request_id: str | None = None
    ) -> httpx.Response:
        """GET /api/v1/me/notifications/unread-count."""
        return await self.get(
            "/api/v1/me/notifications/unread-count",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def mark_notification_read(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        notification_id: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/me/notifications/{id}/read."""
        return await self.post(
            f"/api/v1/me/notifications/{notification_id}/read",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={},
        )

    async def mark_all_notifications_read(
        self, *, actor_id: uuid.UUID, role: str, request_id: str | None = None
    ) -> httpx.Response:
        """POST /api/v1/me/notifications/read-all."""
        return await self.post(
            "/api/v1/me/notifications/read-all",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={},
        )
