# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Auth-service endpoints for key rotation tracking.

GET  /api/v1/system/keys
     Return last rotation record per key_id.
     Requires internal JWT with role=super_admin.

POST /api/v1/system/keys/{key_id}/rotated
     Record that a key was manually rotated.
     Body: {rotated_at: ISO-8601, note: str | None}
     Requires internal JWT with role=super_admin.

These endpoints are internal-only (no external JWT).  The BFF gates
on super_admin role AND TOTP before forwarding.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from lotsman_shared.internal_jwt import InternalJWTClaims
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from auth_service.api.deps import DbSession, RequireActor
from auth_service.db.models import KeyRotation

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/system", tags=["system"])

_ALLOWED_ROLES = frozenset({"super_admin"})


def _require_super_admin(actor: RequireActor) -> InternalJWTClaims:
    if actor.role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Forbidden: super_admin role required")
    return actor


RequireSuperAdmin = Depends(_require_super_admin)


class RecordRotationBody(BaseModel):
    rotated_at: datetime
    note: str | None = None


@router.get("/keys")
async def list_key_rotations(
    actor: RequireActor,
    db: DbSession,
) -> list[dict[str, Any]]:
    """Return last rotation record for each tracked key_id."""
    if actor.role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Forbidden: super_admin role required")

    result = await db.execute(select(KeyRotation).order_by(KeyRotation.key_id))
    rows = result.scalars().all()

    log.info("key_rotations_listed", count=len(rows), actor_id=str(actor.actor_id))
    return [
        {
            "key_id": r.key_id,
            "rotated_at": r.rotated_at.isoformat(),
            "rotated_by": str(r.rotated_by),
            "note": r.note,
        }
        for r in rows
    ]


@router.post("/keys/{key_id}/rotated", status_code=200)
async def record_key_rotation(
    key_id: str,
    body: RecordRotationBody,
    actor: RequireActor,
    db: DbSession,
) -> dict[str, Any]:
    """Record that a cryptographic key was manually rotated.

    Uses an UPSERT so that recording a rotation for an existing key_id
    updates the record rather than failing.
    """
    if actor.role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Forbidden: super_admin role required")

    if not key_id or len(key_id) > 128:
        raise HTTPException(status_code=422, detail="key_id must be 1–128 characters")

    stmt = pg_insert(KeyRotation).values(
        key_id=key_id,
        rotated_at=body.rotated_at,
        rotated_by=actor.actor_id,
        note=body.note,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["key_id"],
        set_={
            "rotated_at": stmt.excluded.rotated_at,
            "rotated_by": stmt.excluded.rotated_by,
            "note": stmt.excluded.note,
        },
    )

    await db.execute(stmt)
    await db.commit()

    log.info(
        "key_rotation_recorded",
        key_id=key_id,
        rotated_at=body.rotated_at.isoformat(),
        actor_id=str(actor.actor_id),
    )

    return {
        "key_id": key_id,
        "rotated_at": body.rotated_at.isoformat(),
        "rotated_by": str(actor.actor_id),
        "note": body.note,
    }
