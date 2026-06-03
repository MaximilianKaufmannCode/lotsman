# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""POST /v1/migrate — run alembic upgrade head inside a whitelisted container.

SECURITY:
- Service name validated against ALLOWED_SERVICE_NAMES before any Docker call.
- The exec command is HARDCODED (ALEMBIC_UPGRADE_CMD tuple from whitelist.py).
  No user input is interpolated into the exec args.
- Uses Docker SDK container.exec_run() — no subprocess, no shell=True.
- Output is captured and tailed (last 50 lines) for the response.  Stdout is
  NOT logged to avoid leaking DB connection strings that alembic may print.
"""

from __future__ import annotations

import time
from typing import Any

import docker
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from system_control.auth import RequireInternalJWT
from system_control.domain.whitelist import (
    ALEMBIC_UPGRADE_CMD,
    ALEMBIC_WORKDIR,
    ALLOWED_SERVICE_NAMES,
    SERVICE_TO_CONTAINER,
)

log = structlog.get_logger(__name__)
router = APIRouter(tags=["ops"])

_OUTPUT_TAIL_LINES = 50


class MigrateRequest(BaseModel):
    service: str


@router.post("/migrate")
async def run_migrate(
    body: MigrateRequest,
    claims: RequireInternalJWT,
) -> dict[str, Any]:
    """Run `alembic upgrade head` inside a whitelisted service container.

    The Alembic command is hardcoded; no user input is passed as command args.
    Returns exit_code, output_tail (last 50 lines), and duration_ms.
    """
    if body.service not in ALLOWED_SERVICE_NAMES:
        log.warning(
            "migrate_service_not_allowed",
            service=body.service,
            actor_id=str(claims.actor_id),
        )
        raise HTTPException(
            status_code=400,
            detail=f"Service '{body.service}' is not in the allowed list",
        )

    container_name = SERVICE_TO_CONTAINER[body.service]

    log.info(
        "migrate_requested",
        service=body.service,
        container=container_name,
        actor_id=str(claims.actor_id),
        jti=claims.jti,
    )

    start = time.monotonic()
    try:
        client = docker.from_env()
        container = client.containers.get(container_name)

        # exec_run takes a tuple/list — no shell interpolation.
        exec_result = container.exec_run(
            cmd=list(ALEMBIC_UPGRADE_CMD),
            workdir=ALEMBIC_WORKDIR,
            demux=False,
        )
    except docker.errors.NotFound as exc:
        raise HTTPException(
            status_code=404, detail=f"Container '{container_name}' not found"
        ) from exc
    except Exception as exc:
        log.error(
            "migrate_docker_error",
            service=body.service,
            container=container_name,
            error=str(exc),
            actor_id=str(claims.actor_id),
        )
        raise HTTPException(status_code=503, detail=f"Docker error: {exc}") from exc

    duration_ms = int((time.monotonic() - start) * 1000)

    # exec_result.output is bytes; decode safely.
    raw_output: bytes = exec_result.output or b""
    output_lines = raw_output.decode("utf-8", errors="replace").splitlines()
    output_tail = output_lines[-_OUTPUT_TAIL_LINES:]
    exit_code: int = exec_result.exit_code if exec_result.exit_code is not None else -1

    log.info(
        "migrate_completed",
        service=body.service,
        container=container_name,
        exit_code=exit_code,
        duration_ms=duration_ms,
        actor_id=str(claims.actor_id),
        # NOT logging output — may contain DB connection strings.
    )

    return {
        "exit_code": exit_code,
        "output_tail": output_tail,
        "duration_ms": duration_ms,
    }
