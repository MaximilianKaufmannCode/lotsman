# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Async HTTP client for system-control sidecar.

The system-control sidecar uses a different JWT audience ('system-control')
from the standard backend services.  This client extends DownstreamClient and
overrides the AUDIENCE to match.

All requests are authenticated via the per-sidecar HS256 key
(INTERNAL_JWT_KEY_SYSTEM_CONTROL), which is distinct from any other key per
ADR-0003 §10 (F-002).

Timeouts are longer than normal downstream calls (backup can take up to 10 min).
"""

from __future__ import annotations

from typing import Any

import httpx

from web_bff.infrastructure.clients.base import DownstreamClient

# Separate timeout profile for potentially long-running ops.
_OPS_TIMEOUT = httpx.Timeout(connect=5.0, read=620.0, write=30.0, pool=5.0)


class SystemControlClient(DownstreamClient):
    """Client for system-control sidecar endpoints.

    Audience: 'system-control' (distinct from all backend services per F-002).
    """

    AUDIENCE = "system-control"

    async def healthz(self) -> httpx.Response:
        """GET /healthz — liveness check (no internal JWT required by sidecar)."""
        return await self._client.get("/healthz", timeout=5.0)

    async def ps(
        self,
        *,
        actor_id: Any,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """GET /v1/ps — list containers."""
        return await self.get(
            "/v1/ps",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )

    async def get_logs(
        self,
        *,
        actor_id: Any,
        role: str,
        service: str,
        tail: int = 100,
        request_id: str | None = None,
    ) -> httpx.Response:
        """GET /v1/logs — proxy docker logs for a service."""
        return await self.get(
            "/v1/logs",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            params={"service": service, "tail": str(tail)},
        )

    async def restart_service(
        self,
        *,
        actor_id: Any,
        role: str,
        service: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /v1/restart-service."""
        return await self.post(
            "/v1/restart-service",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={"service": service},
        )

    async def backup_now(
        self,
        *,
        actor_id: Any,
        role: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /v1/backup-now."""
        return await self.post(
            "/v1/backup-now",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={},
        )

    async def migrate(
        self,
        *,
        actor_id: Any,
        role: str,
        service: str,
        request_id: str | None = None,
    ) -> httpx.Response:
        """POST /v1/migrate."""
        return await self.post(
            "/v1/migrate",
            actor_id=actor_id,
            role=role,
            request_id=request_id,
            json={"service": service},
        )
