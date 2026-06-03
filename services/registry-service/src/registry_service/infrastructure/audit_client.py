# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Async HTTP client to audit-service for history queries (US-18, US-19).

Calls GET /api/v1/audit/events?entity_type=<type>&entity_id=<id>&limit=<n>
using the internal JWT minted for 'audit-service' audience.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import structlog
from lotsman_shared.internal_jwt import issue_internal_jwt

log = structlog.get_logger(__name__)


class AuditServiceHttpClient:
    """Implements AuditServiceClient protocol via httpx."""

    def __init__(
        self,
        base_url: str,
        signing_key: str,
        ttl_seconds: int = 60,
        timeout: float = 5.0,
    ) -> None:
        self._base_url = base_url
        self._key = signing_key
        self._ttl = ttl_seconds
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout),
        )

    def _headers(
        self,
        *,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None,
    ) -> dict[str, str]:
        token = issue_internal_jwt(
            self._key,
            actor_id=actor_id,
            role=role,
            audience="audit-service",
            request_id=request_id,
            ttl_seconds=self._ttl,
        )
        headers: dict[str, str] = {"X-Internal-Token": token}
        if request_id:
            headers["X-Request-Id"] = request_id
        return headers

    async def get_events(
        self,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        limit: int = 50,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            # audit-svc mounts its router at `/api/v1` + router prefix `/audit`
            # → final path is `/api/v1/audit/events`. The earlier "/api/v1/events"
            # was a regression that returned 404 silently (caught below) and made
            # GetDocumentHistory always return [].
            resp = await self._client.get(
                "/api/v1/audit/events",
                headers=self._headers(actor_id=actor_id, role=role, request_id=request_id),
                params={
                    "entity_type": entity_type,
                    "entity_id": str(entity_id),
                    "limit": limit,
                },
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            log.warning(
                "audit_client_http_error",
                status=exc.response.status_code,
                entity_type=entity_type,
                entity_id=str(entity_id),
            )
            return []
        except Exception:
            log.exception(
                "audit_client_error",
                entity_type=entity_type,
                entity_id=str(entity_id),
            )
            return []

    async def aclose(self) -> None:
        await self._client.aclose()
