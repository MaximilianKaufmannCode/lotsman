# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""HTTP client for auth-service /api/v1/internal/users/* (bulk user lookup).

Used by notification-service's reminder scheduler to resolve user_id → email,
full_name before sending an email reminder. Mirrors `registry_gateway.py`
auth model: HS256 internal JWT signed with INTERNAL_JWT_KEY_AUTH via shared
issue_internal_jwt helper.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import structlog
from lotsman_shared.internal_jwt import issue_internal_jwt

log = structlog.get_logger(__name__)

# Same system-actor UUID as registry_gateway — consistent identity for all
# notification-service → auth-service / registry-service internal calls.
_SYSTEM_ACTOR = uuid.UUID("00000000-0000-0000-0000-000000000002")
_SYSTEM_ROLE = "admin"


class HttpAuthGateway:
    """Read-only HTTP gateway to auth-service for internal data."""

    def __init__(
        self,
        *,
        base_url: str,
        signing_key: str,
        ttl_seconds: int = 60,
        timeout_s: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._signing_key = signing_key
        self._ttl = ttl_seconds
        self._client = httpx.AsyncClient(timeout=timeout_s)

    def _auth_header(self) -> dict[str, str]:
        token = issue_internal_jwt(
            self._signing_key,
            actor_id=_SYSTEM_ACTOR,
            role=_SYSTEM_ROLE,
            audience="auth-service",
            ttl_seconds=self._ttl,
        )
        return {"X-Internal-Token": token}

    async def lookup_users(
        self, user_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, Any]]:
        """Bulk-lookup users by ID. Returns {user_id: {id, email, full_name, role}}.

        Missing user_ids are silently dropped from the result (the endpoint
        returns only found users — that's the auth-svc contract).
        """
        if not user_ids:
            return {}

        url = f"{self._base_url}/api/v1/internal/users/lookup"
        # auth-svc contract (see /api/v1/internal/users/lookup):
        #   request:  {"ids": [<uuid-str>, ...]}      ← NOT "user_ids"
        #   response: {<uuid-str>: {"id", "full_name", "email", "is_active"}}
        try:
            resp = await self._client.post(
                url,
                headers=self._auth_header(),
                json={"ids": [str(uid) for uid in user_ids]},
            )
        except httpx.HTTPError as exc:
            log.warning(
                "auth_gateway.lookup_users_http_error",
                error_class=type(exc).__name__,
                count=len(user_ids),
            )
            return {}

        if not resp.is_success:
            log.warning(
                "auth_gateway.lookup_users_non_2xx",
                status=resp.status_code,
                count=len(user_ids),
            )
            return {}

        data = resp.json()
        out: dict[uuid.UUID, dict[str, Any]] = {}
        if isinstance(data, dict):
            for key, item in data.items():
                try:
                    uid = uuid.UUID(key)
                    out[uid] = item
                except (ValueError, TypeError):
                    continue
        return out

    async def list_active_users(self) -> list[dict[str, Any]]:
        """List active users for reminder fan-out (ADR-0011 §D3).

        Calls auth-svc ``GET /api/v1/internal/users?active=true``. Returns a list
        of ``{id, email, full_name, is_active, role}``. On any error returns an
        empty list — the caller treats that as "no fan-out recipients" and the
        scheduler falls back to responsible-only.
        """
        url = f"{self._base_url}/api/v1/internal/users"
        try:
            resp = await self._client.get(
                url,
                headers=self._auth_header(),
                params={"active": "true"},
            )
        except httpx.HTTPError as exc:
            log.warning(
                "auth_gateway.list_active_users_http_error",
                error_class=type(exc).__name__,
            )
            return []

        if not resp.is_success:
            log.warning(
                "auth_gateway.list_active_users_non_2xx",
                status=resp.status_code,
            )
            return []

        data = resp.json()
        return list(data) if isinstance(data, list) else []

    async def aclose(self) -> None:
        await self._client.aclose()
