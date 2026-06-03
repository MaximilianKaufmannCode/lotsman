# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Per-user notification preferences API (ADR-0011 §D2 / §D3).

Called service-to-service by web-bff with an internal JWT whose ``actor_id`` is
the end user. The user only ever reads/writes their OWN preferences — the row is
keyed by ``actor_id``, there is no way to address another user's prefs here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from lotsman_shared.envelope import make_envelope
from pydantic import BaseModel, Field

from notification_service.api.deps import CurrentActor, DbSession
from notification_service.domain.notification_prefs import (
    DEFAULT_EMAIL_MODE,
    VALID_EMAIL_MODES,
    effective,
    sanitize_categories,
)
from notification_service.domain.notification_prefs import (
    EffectivePrefs as _EffectivePrefs,
)
from notification_service.infrastructure.db.repositories import (
    SqlaEventOutbox,
    SqlaUserNotificationPrefRepository,
    SqlaUserNotificationRepository,
)

router = APIRouter(prefix="/me", tags=["me"])


class CategoryPref(BaseModel):
    in_app: bool
    email: bool


class PrefsResponse(BaseModel):
    enabled: bool
    suppress_own: bool
    email_mode: str
    categories: dict[str, CategoryPref]


class PrefsUpdate(BaseModel):
    enabled: bool = True
    suppress_own: bool = True
    email_mode: str = DEFAULT_EMAIL_MODE
    categories: dict[str, dict[str, bool]] = Field(default_factory=dict)


def _to_response(eff: _EffectivePrefs) -> PrefsResponse:
    return PrefsResponse(
        enabled=eff.enabled,
        suppress_own=eff.suppress_own,
        email_mode=eff.email_mode,
        categories={k: CategoryPref(**v) for k, v in eff.categories.items()},
    )


@router.get("/notification-prefs", response_model=PrefsResponse)
async def get_my_prefs(actor: CurrentActor, db: DbSession) -> PrefsResponse:
    """Return the caller's effective preferences (defaults if none saved yet)."""
    if actor is None:
        raise HTTPException(status_code=401, detail="Internal token required")
    repo = SqlaUserNotificationPrefRepository(db)
    row = await repo.get(actor.actor_id)
    return _to_response(effective(row))


@router.put("/notification-prefs", response_model=PrefsResponse)
async def put_my_prefs(
    body: PrefsUpdate, actor: CurrentActor, db: DbSession
) -> PrefsResponse:
    """Upsert the caller's preferences. Unknown categories/channels are dropped."""
    if actor is None:
        raise HTTPException(status_code=401, detail="Internal token required")
    if body.email_mode not in VALID_EMAIL_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"email_mode must be one of {sorted(VALID_EMAIL_MODES)}",
        )
    async with db.begin():
        repo = SqlaUserNotificationPrefRepository(db)
        row = await repo.upsert(
            user_id=actor.actor_id,
            enabled=body.enabled,
            suppress_own=body.suppress_own,
            email_mode=body.email_mode,
            categories=sanitize_categories(body.categories),
        )
        # Audit the preference change via outbox (the project conventions) — same txn (C4).
        # Consistent with create/delete_saved_filter & calendar-subscription.
        await SqlaEventOutbox(db).publish(
            make_envelope(
                event_type="notification.prefs.updated.v1",
                actor_id=actor.actor_id,
                payload={
                    "user_id": str(actor.actor_id),
                    "enabled": body.enabled,
                    "suppress_own": body.suppress_own,
                    "email_mode": body.email_mode,
                },
            )
        )
        # Build the response while the row is still attached to the live txn.
        resp = _to_response(effective(row))
    return resp


# ── In-app notification feed (ADR-0011 §D6) ──────────────────────────────────


class NotificationItem(BaseModel):
    id: uuid.UUID
    category: str
    document_id: uuid.UUID | None
    title: str
    body: str
    is_read: bool
    created_at: datetime


class NotificationFeed(BaseModel):
    items: list[NotificationItem]
    unread: int


@router.get("/notifications", response_model=NotificationFeed)
async def list_my_notifications(
    actor: CurrentActor,
    db: DbSession,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> NotificationFeed:
    """Return the caller's in-app feed (newest first) + unread count."""
    if actor is None:
        raise HTTPException(status_code=401, detail="Internal token required")
    repo = SqlaUserNotificationRepository(db)
    rows = await repo.list_for_user(actor.actor_id, limit=limit, offset=offset)
    unread = await repo.count_unread(actor.actor_id)
    items = [
        NotificationItem(
            id=r.id,
            category=r.category,
            document_id=r.document_id,
            title=r.title,
            body=r.body,
            is_read=r.is_read,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return NotificationFeed(items=items, unread=unread)


@router.get("/notifications/unread-count")
async def my_unread_count(actor: CurrentActor, db: DbSession) -> dict[str, int]:
    """Lightweight unread counter for the header bell badge."""
    if actor is None:
        raise HTTPException(status_code=401, detail="Internal token required")
    repo = SqlaUserNotificationRepository(db)
    return {"unread": await repo.count_unread(actor.actor_id)}


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: uuid.UUID, actor: CurrentActor, db: DbSession
) -> dict[str, bool]:
    """Mark one notification read (only if it belongs to the caller)."""
    if actor is None:
        raise HTTPException(status_code=401, detail="Internal token required")
    async with db.begin():
        repo = SqlaUserNotificationRepository(db)
        ok = await repo.mark_read(notification_id=notification_id, user_id=actor.actor_id)
    return {"ok": ok}


@router.post("/notifications/read-all")
async def mark_all_notifications_read(
    actor: CurrentActor, db: DbSession
) -> dict[str, int]:
    """Mark all of the caller's notifications read."""
    if actor is None:
        raise HTTPException(status_code=401, detail="Internal token required")
    async with db.begin():
        repo = SqlaUserNotificationRepository(db)
        count = await repo.mark_all_read(actor.actor_id)
    return {"marked": count}
