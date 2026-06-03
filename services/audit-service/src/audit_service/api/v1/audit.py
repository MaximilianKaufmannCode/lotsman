# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Audit log query endpoints.

GET /api/v1/audit/events
    General audit event log with filters: entity_type, entity_id, actor,
    event_type, from, to, limit (max 200).
    Requires authenticated actor.

GET /api/v1/audit/system
    Pre-filtered to system-relevant event types only (policy violations,
    system commands, key rotation records, etc.).
    Requires authenticated actor with role=super_admin.
    Accepts: from, to, actor, type, limit (max 100).

All endpoints return paginated lists; no cursors at this stage (simple
OFFSET-free queries ordered by occurred_at DESC, capped by limit).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from lotsman_shared.internal_jwt import InternalJWTClaims
from sqlalchemy import and_, or_, select

from audit_service.api.deps import DbSession, current_actor

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/audit", tags=["audit"])

# ---------------------------------------------------------------------------
# System event type filter — pre-approved list, never constructed from user input
# ---------------------------------------------------------------------------

SYSTEM_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "auth.user.bootstrapped.v1",
        "auth.policy.violation.v1",
        "notification.channel.auto_disabled.v1",
        "notification.channel.rekeyed.v1",
        "notification.calendar.conflict_resolved.v1",
        "system.key.rotation_recorded.v1",
        # All system.command.* subtypes are matched by prefix in the query below.
        # We add the prefix constant here so it is visible for review.
        # Actual prefix filtering is done in the query via LIKE.
    }
)

# Prefix for system.command.* events (matched via LIKE in SQL)
_SYSTEM_COMMAND_PREFIX = "system.command."

_MAX_GENERAL_LIMIT = 200
_MAX_SYSTEM_LIMIT = 100

_SUPER_ADMIN_ROLE = "super_admin"


async def _require_actor(
    actor: Annotated[InternalJWTClaims | None, Depends(current_actor)],
) -> InternalJWTClaims:
    if actor is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return actor


RequireActor = Annotated[InternalJWTClaims, Depends(_require_actor)]


async def _require_super_admin(
    actor: Annotated[InternalJWTClaims, Depends(_require_actor)],
) -> InternalJWTClaims:
    if actor.role != _SUPER_ADMIN_ROLE:
        raise HTTPException(status_code=403, detail="Forbidden: super_admin role required")
    return actor


RequireSuperAdmin = Annotated[InternalJWTClaims, Depends(_require_super_admin)]


# ---------------------------------------------------------------------------
# GET /api/v1/audit/events — general audit log
# ---------------------------------------------------------------------------


@router.get("/events")
async def list_audit_events(
    actor: RequireActor,
    db: DbSession,
    entity_type: Annotated[str | None, Query()] = None,
    entity_id: Annotated[str | None, Query()] = None,
    actor_id: Annotated[str | None, Query(alias="actor")] = None,
    event_type: Annotated[str | None, Query()] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_GENERAL_LIMIT)] = 50,
) -> list[dict[str, Any]]:
    """Query audit events with optional filters."""
    from audit_service.db.models import AuditEvent

    conditions = []
    if entity_type:
        conditions.append(AuditEvent.entity_type == entity_type)
    if entity_id:
        try:
            import uuid as _uuid
            eid = _uuid.UUID(entity_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="entity_id must be a valid UUID") from exc
        conditions.append(AuditEvent.entity_id == eid)
    if actor_id:
        try:
            import uuid as _uuid
            aid = _uuid.UUID(actor_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="actor must be a valid UUID") from exc
        conditions.append(AuditEvent.actor_id == aid)
    if event_type:
        conditions.append(AuditEvent.event_type == event_type)
    if from_:
        conditions.append(AuditEvent.occurred_at >= from_)
    if to:
        conditions.append(AuditEvent.occurred_at <= to)

    stmt = (
        select(AuditEvent)
        .where(and_(*conditions) if conditions else True)  # type: ignore[arg-type]
        .order_by(AuditEvent.occurred_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    log.info(
        "audit_events_listed",
        count=len(rows),
        actor_id=str(actor.actor_id),
        filters={
            "entity_type": entity_type,
            "event_type": event_type,
            "limit": limit,
        },
    )

    return [_event_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# GET /api/v1/audit/system — system-filtered audit log (super_admin only)
# ---------------------------------------------------------------------------


@router.get("/system")
async def list_system_audit_events(
    actor: RequireSuperAdmin,
    db: DbSession,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    actor_id: Annotated[str | None, Query(alias="actor")] = None,
    event_type: Annotated[str | None, Query(alias="type")] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_SYSTEM_LIMIT)] = 50,
) -> list[dict[str, Any]]:
    """Return audit events filtered to system-relevant event types.

    Matches:
    - Exact event_type values in SYSTEM_EVENT_TYPES
    - Any event_type starting with 'system.command.'

    If the caller additionally passes `type`, it is intersected with the
    system filter (i.e. the caller can narrow further, but never expand beyond
    the system event set).
    """
    from audit_service.db.models import AuditEvent

    # Build the system-type filter: exact matches OR prefix match.
    exact_types = list(SYSTEM_EVENT_TYPES)
    type_filter = or_(
        AuditEvent.event_type.in_(exact_types),
        AuditEvent.event_type.like(f"{_SYSTEM_COMMAND_PREFIX}%"),
    )

    conditions = [type_filter]

    if actor_id:
        try:
            import uuid as _uuid
            aid = _uuid.UUID(actor_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="actor must be a valid UUID") from exc
        conditions.append(AuditEvent.actor_id == aid)
    if from_:
        conditions.append(AuditEvent.occurred_at >= from_)
    if to:
        conditions.append(AuditEvent.occurred_at <= to)

    # Caller-supplied `type` must be a subset of SYSTEM_EVENT_TYPES or the prefix.
    if event_type:
        if not (
            event_type in SYSTEM_EVENT_TYPES
            or event_type.startswith(_SYSTEM_COMMAND_PREFIX)
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"event_type '{event_type}' is not a system event type. "
                    "Allowed: system event types and system.command.* prefix."
                ),
            )
        conditions.append(AuditEvent.event_type == event_type)

    stmt = (
        select(AuditEvent)
        .where(and_(*conditions))
        .order_by(AuditEvent.occurred_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    log.info(
        "system_audit_events_listed",
        count=len(rows),
        actor_id=str(actor.actor_id),
        filters={"from": str(from_), "to": str(to), "limit": limit},
    )

    return [_event_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event_to_dict(event: Any) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "occurred_at": event.occurred_at.isoformat(),
        "actor_id": str(event.actor_id),
        "entity_type": event.entity_type,
        "entity_id": str(event.entity_id),
        "event_type": event.event_type,
        "payload": event.payload,
        "request_id": event.request_id,
    }
