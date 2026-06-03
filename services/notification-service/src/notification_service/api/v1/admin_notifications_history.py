# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Admin notification-delivery history endpoint — /api/v1/admin/notifications/history

Read-only. Returns paginated delivery_attempts rows with filters.
Re-MFA NOT required (read-only). Admin role enforced at BFF.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from lotsman_shared.internal_jwt import InternalJWTClaims

from notification_service.api.deps import DbSession, current_actor
from notification_service.application.use_cases.list_delivery_history import (
    ListDeliveryHistory,
    ListDeliveryHistoryQuery,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/notifications", tags=["admin-notifications"])


def _require_actor(
    actor: Annotated[InternalJWTClaims | None, Depends(current_actor)],
) -> InternalJWTClaims:
    if actor is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return actor


@router.get("/history")
async def get_delivery_history(
    actor: Annotated[InternalJWTClaims, Depends(_require_actor)],
    db: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, pattern="^(pending|sent|failed)$"),
    template_code: str | None = Query(None, pattern="^(pre_notice|in_day|overdue)$"),
    channel: str | None = Query(None, pattern="^(email|telegram|dion)$"),
    document_id: uuid.UUID | None = Query(None),
    user_id: uuid.UUID | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
) -> dict[str, Any]:
    """Paginated history of notification.delivery_attempts."""
    use_case = ListDeliveryHistory(session=db)
    result = await use_case.execute(
        query=ListDeliveryHistoryQuery(
            limit=limit,
            offset=offset,
            status=status,
            template_code=template_code,
            channel=channel,
            document_id=document_id,
            user_id=user_id,
            date_from=date_from,
            date_to=date_to,
        )
    )
    log.info(
        "admin.notifications_history.listed",
        actor_id=str(actor.actor_id),
        total=result["total"],
        returned=len(result["items"]),
    )
    return result
