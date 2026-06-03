# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""POST /v1/restart-service — restart a whitelisted Lotsman container.

SECURITY:
- Service name validated against ALLOWED_SERVICE_NAMES before any Docker call.
- Uses Docker SDK (docker.from_env().containers.get(name).restart()) — no
  subprocess, no shell=True, no f-string interpolation into commands.
- Structured log line emitted after each operation (stdout NOT logged).
"""

from __future__ import annotations

import time
from typing import Any

import docker
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from system_control.auth import RequireInternalJWT
from system_control.domain.whitelist import ALLOWED_SERVICE_NAMES, SERVICE_TO_CONTAINER

log = structlog.get_logger(__name__)
router = APIRouter(tags=["ops"])


class RestartRequest(BaseModel):
    service: str


@router.post("/restart-service")
async def restart_service(
    body: RestartRequest,
    claims: RequireInternalJWT,
) -> dict[str, Any]:
    """Restart a whitelisted Lotsman container.

    Validates `service` against the hardcoded whitelist before issuing the
    Docker restart.  Returns exit code and duration; does NOT return container
    logs to prevent accidental secret leakage in the response.
    """
    if body.service not in ALLOWED_SERVICE_NAMES:
        log.warning(
            "restart_service_not_allowed",
            service=body.service,
            actor_id=str(claims.actor_id),
        )
        raise HTTPException(
            status_code=400,
            detail=f"Service '{body.service}' is not in the allowed list",
        )

    container_name = SERVICE_TO_CONTAINER[body.service]

    log.info(
        "restart_service_requested",
        service=body.service,
        container=container_name,
        actor_id=str(claims.actor_id),
        jti=claims.jti,
    )

    start = time.monotonic()
    try:
        client = docker.from_env()
        container = client.containers.get(container_name)
        container.restart()
    except docker.errors.NotFound as exc:
        raise HTTPException(
            status_code=404, detail=f"Container '{container_name}' not found"
        ) from exc
    except Exception as exc:
        log.error(
            "restart_service_failed",
            service=body.service,
            container=container_name,
            error=str(exc),
            actor_id=str(claims.actor_id),
        )
        raise HTTPException(status_code=503, detail=f"Docker error: {exc}") from exc

    duration_ms = int((time.monotonic() - start) * 1000)

    log.info(
        "restart_service_completed",
        service=body.service,
        container=container_name,
        duration_ms=duration_ms,
        actor_id=str(claims.actor_id),
    )

    return {
        "exit_code": 0,
        "duration_ms": duration_ms,
        "container": container_name,
    }
