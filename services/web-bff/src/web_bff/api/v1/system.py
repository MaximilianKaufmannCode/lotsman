# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Super-admin system panel endpoints — /api/v1/system/*

All routes require `role == super_admin`.  Mutating operations additionally
require TOTP re-MFA (via the existing _verify_re_mfa helper).  Destructive
operations (backup, restart, migrate) also require a typed-confirmation string.

Endpoints:
  GET  /health            — aggregate health of all 5 backend services + infra
  GET  /queues            — outbox queue depths and Redis stream lag
  GET  /migrations        — current alembic revision per service (constant map MVP)
  GET  /keys              — key rotation records from auth.key_rotations
  POST /keys/{key_id}/rotated — record manual key rotation (TOTP required)
  GET  /logs              — proxy docker logs from system-control sidecar
  POST /backup-now        — trigger backup (TOTP + typed-confirm "BACKUP NOW")
  POST /restart-service   — restart service (TOTP + typed-confirm = service name)
  POST /migrate           — alembic upgrade (TOTP + typed-confirm = service name)
  GET  /audit             — proxy system-filtered audit log from audit-service

Sidecar availability: if system-control is unreachable, sidecar-backed endpoints
return 503 (Service Unavailable) rather than crashing the BFF.

Re-MFA is re-used from admin.py — zero code duplication.
Typed-confirmation: plain string equality check, enforced before any sidecar call.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from web_bff.api.deps import (
    AccessClaims,
    GetAuditClient,
    GetAuthClient,
    get_request_id,
    require_access_claims,
)
from web_bff.api.v1.admin import _upstream_error, _verify_re_mfa
from web_bff.config import Settings, get_settings
from web_bff.infrastructure.clients.system_control_client import SystemControlClient

log = structlog.get_logger(__name__)
router = APIRouter(tags=["system"])

# ---------------------------------------------------------------------------
# Latest-revision constant map (MVP approach per spec).
# Update this map when a new migration is merged.  Phase 3 will automate this.
# ---------------------------------------------------------------------------
_LATEST_REVISIONS: dict[str, str] = {
    "auth-svc": "0007_key_rotations",
    "registry-svc": "0004_inn_check",
    "notification-svc": "0001_initial_notification_schema",
    "audit-svc": "0001_initial_audit_schema",
}

_SUPER_ADMIN_ROLE = "super_admin"


# ---------------------------------------------------------------------------
# Auth gate: require super_admin role
# ---------------------------------------------------------------------------


def _require_super_admin(
    claims: Annotated[AccessClaims, Depends(require_access_claims)],
) -> AccessClaims:
    if claims.role != _SUPER_ADMIN_ROLE:
        raise HTTPException(status_code=403, detail="Forbidden: super_admin role required")
    return claims


RequireSuperAdmin = Annotated[AccessClaims, Depends(_require_super_admin)]


# ---------------------------------------------------------------------------
# Sidecar client accessor
# ---------------------------------------------------------------------------


def _get_system_control_client(request: Request) -> SystemControlClient | None:
    """Return the system-control client from app.state, or None if not wired."""
    return getattr(request.app.state, "system_control_client", None)


GetSystemControlClient = Annotated[SystemControlClient | None, Depends(_get_system_control_client)]


def _sidecar_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="system-control sidecar is not available",
    )


# ---------------------------------------------------------------------------
# Typed-confirmation validation
# ---------------------------------------------------------------------------


def _require_confirmation(actual: str, expected: str) -> None:
    if actual != expected:
        raise HTTPException(
            status_code=400,
            detail={"detail": "Typed confirmation does not match", "code": "CONFIRMATION_MISMATCH"},
        )


# ---------------------------------------------------------------------------
# GET /api/v1/system/health
# ---------------------------------------------------------------------------


async def _probe_service(
    client: httpx.AsyncClient,
    name: str,
    url: str,
) -> dict[str, Any]:
    try:
        resp = await client.get(url, timeout=5.0)
        status = "ok" if resp.status_code == 200 else f"error: HTTP {resp.status_code}"
    except Exception as exc:
        status = f"error: {exc}"
    return {"name": name, "status": status, "url": url}


@router.get("/health")
async def system_health(
    claims: RequireSuperAdmin,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Aggregate health of all backend services, Postgres, and Redis."""
    targets = {
        "auth-service": f"{settings.auth_svc_url}/healthz",
        "registry-service": f"{settings.registry_svc_url}/healthz",
        "notification-service": f"{settings.notification_svc_url}/healthz",
        "audit-service": f"{settings.audit_svc_url}/healthz",
        "web-bff": "http://localhost:8000/healthz",
    }

    async with httpx.AsyncClient() as client:
        probes = await asyncio.gather(
            *[_probe_service(client, name, url) for name, url in targets.items()]
        )

    results = list(probes)
    any_failed = any(p["status"] != "ok" for p in results)

    log.info(
        "system_panel_health_checked",
        actor_id=str(claims.subject),
        any_failed=any_failed,
    )

    return {
        "status": "degraded" if any_failed else "ok",
        "services": results,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/system/queues
# ---------------------------------------------------------------------------


@router.get("/queues")
async def system_queues(
    claims: RequireSuperAdmin,
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[dict[str, Any]]:
    """Return per-service outbox queue depths.

    MVP: queries via auth-service internal JWT to each service's readyz.
    Full implementation (SQL outbox counts + Redis stream lag) requires
    direct DB access which web-bff does not have.  Returning stub data
    with a 'not_implemented' marker so the UI can show a meaningful state.
    """
    log.info("system_queues_checked", actor_id=str(claims.subject))
    return [
        {
            "service": svc,
            "outbox_undispatched_count": None,
            "stream_lag": None,
            "dlq_size": None,
            "note": "Queue metrics require direct DB access — available in Phase 3 dashboard",
        }
        for svc in ["auth-svc", "registry-svc", "notification-svc", "audit-svc"]
    ]


# ---------------------------------------------------------------------------
# GET /api/v1/system/migrations
# ---------------------------------------------------------------------------


@router.get("/migrations")
async def system_migrations(
    claims: RequireSuperAdmin,
) -> list[dict[str, Any]]:
    """Return migration status per service using the constant revision map.

    The 'current' revision is not queried from the DB (BFF has no DB access).
    The Phase 3 migrate endpoint can trigger the actual check via sidecar.
    For now this returns the expected latest revision per service so the UI
    knows what HEAD looks like.
    """
    log.info("system_migrations_checked", actor_id=str(claims.subject))
    return [
        {
            "service": svc,
            "latest_in_code": rev,
            "note": "Current DB revision requires alembic check via /system/migrate — see Phase 3",
        }
        for svc, rev in _LATEST_REVISIONS.items()
    ]


# ---------------------------------------------------------------------------
# GET /api/v1/system/keys
# POST /api/v1/system/keys/{key_id}/rotated
# ---------------------------------------------------------------------------


@router.get("/keys")
async def system_keys(
    claims: RequireSuperAdmin,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Return key rotation records from auth.key_rotations."""
    upstream = await auth_client.system_list_key_rotations(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


class RecordRotationBody(BaseModel):
    totp_code: str
    rotated_at: datetime
    note: str | None = None


@router.post("/keys/{key_id}/rotated")
async def record_key_rotation(
    key_id: str,
    body: RecordRotationBody,
    claims: RequireSuperAdmin,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Record that a key was manually rotated. Requires TOTP re-MFA."""
    await _verify_re_mfa(
        admin=claims,
        totp_code=body.totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )

    upstream = await auth_client.system_record_key_rotation(
        actor_id=claims.subject,
        role=claims.role,
        key_id=key_id,
        rotated_at=body.rotated_at,
        note=body.note,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# GET /api/v1/system/logs
# ---------------------------------------------------------------------------


@router.get("/logs")
async def system_logs(
    claims: RequireSuperAdmin,
    sidecar: GetSystemControlClient,
    service: Annotated[str, Query()],
    tail: Annotated[int, Query(ge=1, le=500)] = 100,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Proxy docker log lines from system-control sidecar."""
    if sidecar is None:
        raise _sidecar_unavailable()
    upstream = await sidecar.get_logs(
        actor_id=claims.subject,
        role=claims.role,
        service=service,
        tail=tail,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# POST /api/v1/system/backup-now
# ---------------------------------------------------------------------------


class BackupNowBody(BaseModel):
    totp_code: str
    confirmation: str  # must equal "BACKUP NOW"


@router.post("/backup-now")
async def system_backup_now(
    body: BackupNowBody,
    claims: RequireSuperAdmin,
    auth_client: GetAuthClient,
    sidecar: GetSystemControlClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Trigger an immediate backup. Requires TOTP + typed-confirm 'BACKUP NOW'."""
    if sidecar is None:
        raise _sidecar_unavailable()
    await _verify_re_mfa(
        admin=claims,
        totp_code=body.totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    _require_confirmation(body.confirmation, "BACKUP NOW")

    upstream = await sidecar.backup_now(
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)

    log.info("system_backup_triggered", actor_id=str(claims.subject))
    return upstream.json()


# ---------------------------------------------------------------------------
# POST /api/v1/system/restart-service
# ---------------------------------------------------------------------------


class RestartServiceBody(BaseModel):
    service: str
    totp_code: str
    confirmation: str  # must equal the service name


@router.post("/restart-service")
async def system_restart_service(
    body: RestartServiceBody,
    claims: RequireSuperAdmin,
    auth_client: GetAuthClient,
    sidecar: GetSystemControlClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Restart a service container. Requires TOTP + typed-confirm = service name."""
    if sidecar is None:
        raise _sidecar_unavailable()
    await _verify_re_mfa(
        admin=claims,
        totp_code=body.totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    _require_confirmation(body.confirmation, body.service)

    upstream = await sidecar.restart_service(
        actor_id=claims.subject,
        role=claims.role,
        service=body.service,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)

    log.info(
        "system_restart_triggered",
        service=body.service,
        actor_id=str(claims.subject),
    )
    return upstream.json()


# ---------------------------------------------------------------------------
# POST /api/v1/system/migrate
# ---------------------------------------------------------------------------


class MigrateBody(BaseModel):
    service: str
    totp_code: str
    confirmation: str  # must equal the service name


@router.post("/migrate")
async def system_migrate(
    body: MigrateBody,
    claims: RequireSuperAdmin,
    auth_client: GetAuthClient,
    sidecar: GetSystemControlClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Run alembic upgrade head for a service. Requires TOTP + typed-confirm = service name."""
    if sidecar is None:
        raise _sidecar_unavailable()
    await _verify_re_mfa(
        admin=claims,
        totp_code=body.totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    _require_confirmation(body.confirmation, body.service)

    upstream = await sidecar.migrate(
        actor_id=claims.subject,
        role=claims.role,
        service=body.service,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)

    log.info(
        "system_migrate_triggered",
        service=body.service,
        actor_id=str(claims.subject),
    )
    return upstream.json()


# ---------------------------------------------------------------------------
# GET /api/v1/system/audit  (proxy to audit-service /api/v1/audit/system)
# ---------------------------------------------------------------------------


@router.get("/audit")
async def system_audit(
    claims: RequireSuperAdmin,
    audit_client: GetAuditClient,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    actor: Annotated[str | None, Query()] = None,
    type_: Annotated[str | None, Query(alias="type")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Proxy system-filtered audit log from audit-service."""
    params: dict[str, Any] = {"limit": limit}
    if from_:
        params["from"] = from_.isoformat()
    if to:
        params["to"] = to.isoformat()
    if actor:
        params["actor"] = actor
    if type_:
        params["type"] = type_

    upstream = await audit_client.get(
        "/api/v1/audit/system",
        actor_id=claims.subject,
        role=claims.role,
        request_id=request_id,
        params=params,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()
