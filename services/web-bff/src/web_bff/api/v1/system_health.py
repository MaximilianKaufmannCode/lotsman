# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Aggregated system health endpoint for web-bff.

GET /api/v1/system/health
    Returns the /healthz status of each downstream service.
    This proves cross-service HTTP plumbing end-to-end at scaffold stage.

    Response shape:
        {
            "status": "ok" | "degraded",
            "services": {
                "auth-service":          "ok" | "error: <msg>",
                "registry-service":      "ok" | "error: <msg>",
                "notification-service":  "ok" | "error: <msg>",
                "audit-service":         "ok" | "error: <msg>"
            }
        }
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

import httpx
import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from web_bff.config import Settings, get_settings

log = structlog.get_logger(__name__)
router = APIRouter()

AppSettings = Annotated[Settings, Depends(get_settings)]


async def _probe(client: httpx.AsyncClient, url: str, name: str) -> tuple[str, str]:
    """Probe a single downstream /healthz. Returns (name, 'ok' | 'error: ...')."""
    try:
        resp = await client.get(url, timeout=5.0)
        if resp.status_code == 200:
            return name, "ok"
        return name, f"error: HTTP {resp.status_code}"
    except Exception as exc:
        return name, f"error: {exc}"


@router.get("/health", summary="Aggregated downstream health check")
async def system_health(settings: AppSettings) -> JSONResponse:
    """Probe each downstream service's /healthz in parallel."""
    targets = {
        "auth-service": f"{settings.auth_svc_url}/healthz",
        "registry-service": f"{settings.registry_svc_url}/healthz",
        "notification-service": f"{settings.notification_svc_url}/healthz",
        "audit-service": f"{settings.audit_svc_url}/healthz",
    }

    async with httpx.AsyncClient() as client:
        results: dict[str, Any] = dict(
            await asyncio.gather(*[_probe(client, url, name) for name, url in targets.items()])
        )

    any_failed = any(v != "ok" for v in results.values())
    status_code = 503 if any_failed else 200

    log.info(
        "system_health_checked",
        results=results,
        any_failed=any_failed,
    )

    return JSONResponse(
        content={
            "status": "degraded" if any_failed else "ok",
            "services": results,
        },
        status_code=status_code,
    )
