# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Internal cross-service endpoints.

These routes are NOT exposed via the public BFF chokepoint for SPA traffic —
they are called service-to-service with an internal JWT (audience='auth-service')
and intentionally have no role gate (any valid internal-actor JWT is accepted).
The trust boundary is the internal network + the signed token.

Routes:
  POST /api/v1/internal/users/lookup — bulk lookup user names by IDs
  GET  /api/v1/internal/users        — list users (optionally active-only)
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from auth_service.api.deps import DbSession, RequireActor
from auth_service.infrastructure.db.repositories import SqlaUserRepository

router = APIRouter(prefix="/internal", tags=["internal"])


class UsersLookupRequest(BaseModel):
    ids: list[uuid.UUID] = Field(..., min_length=1, max_length=100)


@router.post("/users/lookup")
async def users_lookup(
    body: UsersLookupRequest,
    db: DbSession,
    actor: RequireActor,
) -> dict[str, dict[str, Any]]:
    """Bulk-lookup user names by IDs.

    Returns ``{<id>: {"id", "full_name", "email", "is_active"}}`` for each user
    that exists. Missing IDs are simply absent from the response — the caller
    is expected to fall back to a placeholder for those.

    Used by web-bff to enrich the registry document-history view with actor
    full names (see ADR-style note in registry's UserDirectoryClient).
    """
    if len(body.ids) > 100:
        raise HTTPException(status_code=422, detail="Max 100 ids per call")
    repo = SqlaUserRepository(db)
    result: dict[str, dict[str, Any]] = {}
    for uid in body.ids:
        user = await repo.get_by_id(uid)
        if user is None:
            continue
        result[str(uid)] = {
            "id": str(user.id),
            "full_name": user.full_name,
            "email": user.email,
            "is_active": user.is_active,
        }
    return result


@router.get("/users")
async def list_internal_users(
    db: DbSession,
    actor: RequireActor,
    active: bool = Query(default=False, description="Return only is_active users."),
) -> list[dict[str, Any]]:
    """List users for cross-service consumers (notification fan-out, ADR-0011 §D3).

    With ``active=true`` returns only enabled accounts. Always usable by any valid
    internal-actor JWT (audience='auth-service') — same trust boundary as
    ``/users/lookup``. Returns ``[{"id","email","full_name","is_active","role"}]``.
    """
    repo = SqlaUserRepository(db)
    users = await repo.list_all()
    out: list[dict[str, Any]] = []
    for user in users:
        if active and not user.is_active:
            continue
        out.append(
            {
                "id": str(user.id),
                "email": user.email,
                "full_name": user.full_name,
                "is_active": user.is_active,
                "role": user.role,
            }
        )
    return out
