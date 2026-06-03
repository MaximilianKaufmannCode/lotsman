# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""GET /v1/logs — docker logs proxy for whitelisted services.

Query parameters:
  service: short service name (must be in ALLOWED_SERVICE_NAMES)
  tail:    number of lines (1–500, default 100)

SECURITY: `service` is validated against the whitelist and resolved to a
container name before being passed to the Docker SDK.  No f-string
interpolation into shell commands.  `docker.from_env()` uses the SDK's
own protocol, not subprocess.
"""

from __future__ import annotations

from typing import Annotated, Any

import docker
import structlog
from fastapi import APIRouter, HTTPException, Query

from system_control.auth import RequireInternalJWT
from system_control.domain.whitelist import (
    ALLOWED_SERVICE_NAMES,
    MAX_LOG_TAIL,
    SERVICE_TO_CONTAINER,
)

log = structlog.get_logger(__name__)
router = APIRouter(tags=["ops"])


@router.get("/logs")
async def get_logs(
    claims: RequireInternalJWT,
    service: Annotated[str, Query(description="Short service name, e.g. 'auth-svc'")],
    tail: Annotated[int, Query(ge=1, le=MAX_LOG_TAIL)] = 100,
) -> dict[str, Any]:
    """Return the last `tail` lines of stdout/stderr from a whitelisted container."""
    if service not in ALLOWED_SERVICE_NAMES:
        log.warning(
            "logs_service_not_allowed",
            service=service,
            actor_id=str(claims.actor_id),
        )
        raise HTTPException(
            status_code=400,
            detail=f"Service '{service}' is not in the allowed list",
        )

    container_name = SERVICE_TO_CONTAINER[service]

    try:
        client = docker.from_env()
        container = client.containers.get(container_name)
        raw: bytes = container.logs(tail=tail, timestamps=True)
    except docker.errors.NotFound as exc:
        raise HTTPException(
            status_code=404, detail=f"Container '{container_name}' not found"
        ) from exc
    except Exception as exc:
        log.error("logs_docker_error", container=container_name, error=str(exc))
        raise HTTPException(status_code=503, detail=f"Docker error: {exc}") from exc

    lines = raw.decode("utf-8", errors="replace").splitlines()
    truncated = len(lines) >= tail

    log.info(
        "logs_retrieved",
        service=service,
        container=container_name,
        lines=len(lines),
        actor_id=str(claims.actor_id),
    )
    return {"lines": lines, "truncated": truncated}
