# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""POST /v1/backup-now — trigger the host backup script.

SECURITY:
- Script path is taken from Settings (env var), defaulting to /scripts/backup.sh.
- The path is NOT constructed from user input.
- subprocess.run is called with a list (not shell=True), with the exact path
  from settings.  No user data is interpolated into the command args.
- stdout/stderr captured but NOT echoed in the response body to avoid leaking
  paths, hostnames, or credentials.  Only a tail of stdout (last 20 lines) is
  returned so the caller can see whether the backup completed successfully.
- Timeout: 600 seconds (the backup could be large).
"""

from __future__ import annotations

import subprocess
import time
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from system_control.auth import RequireInternalJWT
from system_control.config import get_settings

log = structlog.get_logger(__name__)
router = APIRouter(tags=["ops"])

_STDOUT_TAIL_LINES = 20


class BackupRequest(BaseModel):
    # No body fields required; using a model for forward-compatibility.
    pass


@router.post("/backup-now")
async def backup_now(
    body: BackupRequest,
    claims: RequireInternalJWT,
) -> dict[str, Any]:
    """Trigger the host backup script (/scripts/backup.sh by default).

    The script must exist on the host and be mounted into the container at the
    path configured by `backup_script_path` (default: /scripts/backup.sh).

    Returns exit_code, stdout_tail (last 20 lines), and duration_ms.
    Does NOT log stdout (may contain paths/credentials).
    """
    settings = get_settings()
    script_path = settings.backup_script_path

    log.info(
        "backup_requested",
        script=script_path,
        actor_id=str(claims.actor_id),
        jti=claims.jti,
    )

    start = time.monotonic()
    try:
        # List form — never shell=True. The script path comes from settings, not user input.
        result = subprocess.run(  # noqa: S603
            [script_path],
            capture_output=True,
            timeout=600,
            check=False,
        )
    except FileNotFoundError as exc:
        log.error("backup_script_not_found", script=script_path)
        raise HTTPException(
            status_code=503,
            detail=f"Backup script not found at '{script_path}'. "
            "Ensure the host scripts volume is mounted.",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        log.error("backup_script_timeout", script=script_path)
        raise HTTPException(
            status_code=503, detail="Backup script timed out after 600 seconds"
        ) from exc
    except Exception as exc:
        log.error("backup_script_error", script=script_path, error=str(exc))
        raise HTTPException(status_code=503, detail=f"Backup error: {exc}") from exc

    duration_ms = int((time.monotonic() - start) * 1000)

    stdout_lines = result.stdout.decode("utf-8", errors="replace").splitlines()
    stdout_tail = stdout_lines[-_STDOUT_TAIL_LINES:]

    log.info(
        "backup_completed",
        script=script_path,
        exit_code=result.returncode,
        duration_ms=duration_ms,
        actor_id=str(claims.actor_id),
        # Deliberately NOT logging stdout/stderr — may contain paths/credentials.
    )

    return {
        "exit_code": result.returncode,
        "stdout_tail": stdout_tail,
        "duration_ms": duration_ms,
    }
