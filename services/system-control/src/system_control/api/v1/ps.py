# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""GET /v1/ps — read-only list of running containers.

Returns a filtered view of containers visible through /var/run/docker.sock.
No user-supplied data is used in Docker API calls.
"""

from __future__ import annotations

from typing import Any

import docker
import structlog
from fastapi import APIRouter, HTTPException

from system_control.auth import RequireInternalJWT

log = structlog.get_logger(__name__)
router = APIRouter(tags=["ops"])


@router.get("/ps")
async def list_containers(claims: RequireInternalJWT) -> list[dict[str, Any]]:
    """Return all containers visible to the Docker daemon.

    Caller receives: [{name, status, uptime, image}].
    Filtering to lotsman containers only (by label) is done here to avoid
    leaking unrelated container names.
    """
    try:
        client = docker.from_env()
        containers = client.containers.list(
            all=True,
            filters={"label": "com.docker.compose.project=lotsman"},
        )
    except Exception as exc:
        log.error("ps_docker_error", error=str(exc))
        raise HTTPException(status_code=503, detail=f"Docker error: {exc}") from exc

    result: list[dict[str, Any]] = []
    for c in containers:
        result.append(
            {
                "name": c.name,
                "status": c.status,
                "image": c.image.tags[0] if c.image.tags else str(c.image.id)[:12],
                "uptime": c.attrs.get("State", {}).get("StartedAt", ""),
            }
        )

    log.info("ps_listed", count=len(result), actor_id=str(claims.actor_id))
    return result
