# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Admin calendar subscription management — /api/v1/admin/calendar-subscriptions/*

Manages the whitelist of users who receive Exchange calendar events and
automatically grants/revokes Reviewer permission on the shared calendar
folder via EWS (ADR-0005 §7).

Re-MFA is enforced at the BFF layer before these endpoints are called.

Endpoints:
  GET    /admin/calendar-subscriptions               — list subscriptions (enriched)
  POST   /admin/calendar-subscriptions               — add/enable + EWS grant (re-MFA at BFF)
  DELETE /admin/calendar-subscriptions/{user_id}     — soft-disable + EWS revoke (re-MFA at BFF)
  POST   /admin/calendar-subscriptions/{user_id}/retry-share — retry failed/not_attempted grant
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from lotsman_shared.internal_jwt import InternalJWTClaims
from pydantic import BaseModel

from notification_service.api.deps import AppSettings, DbSession, current_actor
from notification_service.domain.events import (
    CalendarShareFailed,
    CalendarShareGranted,
    CalendarShareNotAttempted,
    CalendarShareRevoked,
)
from notification_service.infrastructure.calendar.ews_share import (
    EwsShareError,
    grant_calendar_share,
    revoke_calendar_share,
)
from notification_service.infrastructure.channel_crypto import ChannelCipher
from notification_service.infrastructure.db.repositories import (
    SqlaCalendarSubscriptionRepository,
    SqlaCredentialRepository,
    SqlaEventOutbox,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/calendar-subscriptions", tags=["admin-calendar-subscriptions"])


def _require_admin(
    actor: Annotated[InternalJWTClaims | None, Depends(current_actor)],
) -> InternalJWTClaims:
    if actor is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if actor.role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    return actor


RequireAdmin = Annotated[InternalJWTClaims, Depends(_require_admin)]


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class AddSubscriptionRequest(BaseModel):
    user_id: uuid.UUID
    user_email: str = ""  # BFF passes the user's email for EWS grant


class SubscriptionResponse(BaseModel):
    user_id: uuid.UUID
    enabled: bool
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    share_status: str
    share_granted_at: datetime | None
    share_error: str | None
    ics_feed_token: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _load_ews_config(db: Any) -> dict[str, Any] | None:
    """Load and decrypt the exchange_calendar channel config, or return None.

    Returns None if the channel is not configured or is disabled.
    Never raises — returns None on any failure (channel absent is not fatal
    for subscription operations; only the share status reflects it).
    """
    try:
        cred_repo = SqlaCredentialRepository(db)
        rows = await cred_repo.get_all()
        ews_row = next(
            (r for r in rows if r.channel == "exchange_calendar" and r.enabled),
            None,
        )
        if ews_row is None:
            return None
        cipher = ChannelCipher()
        result: dict[str, Any] = cipher.decrypt(ews_row.config_enc)
        return result
    except Exception:
        log.warning("ews_config.load_failed", exc_info=True)
        return None


async def _attempt_grant(
    *,
    db: Any,
    actor_id: uuid.UUID,
    user_id: uuid.UUID,
    user_email: str,
) -> None:
    """Try EWS grant and update share_status.  Never raises."""
    repo = SqlaCalendarSubscriptionRepository(db)
    outbox = SqlaEventOutbox(db)

    ews_config = await _load_ews_config(db)

    if ews_config is None:
        await repo.set_share_status(
            user_id=user_id,
            share_status="not_attempted",
            share_error=None,
        )
        await outbox.publish(
            CalendarShareNotAttempted(
                actor_id=actor_id,
                user_id=user_id,
            ).as_envelope()
        )
        log.info(
            "calendar_share.not_attempted",
            user_id=str(user_id),
            reason="exchange_calendar channel not configured or disabled",
        )
        return

    if not user_email:
        # No email provided — cannot call EWS.
        await repo.set_share_status(
            user_id=user_id,
            share_status="not_attempted",
            share_error="user_email not provided — cannot call EWS",
        )
        log.warning(
            "calendar_share.not_attempted",
            user_id=str(user_id),
            reason="user_email not provided",
        )
        return

    try:
        await asyncio.to_thread(
            grant_calendar_share,
            ews_config=ews_config,
            user_email=user_email,
        )
        await repo.set_share_status(
            user_id=user_id,
            share_status="granted",
            share_granted_at=datetime.now(tz=UTC),
            share_error=None,
        )
        await outbox.publish(
            CalendarShareGranted(
                actor_id=actor_id,
                user_id=user_id,
                user_email=user_email,
            ).as_envelope()
        )
        log.info(
            "calendar_share.granted",
            user_id=str(user_id),
            user_email=user_email,
        )
    except (EwsShareError, Exception) as exc:
        err_msg = str(exc) if isinstance(exc, EwsShareError) else f"EWS error: {type(exc).__name__}"
        await repo.set_share_status(
            user_id=user_id,
            share_status="failed",
            share_error=err_msg,
        )
        await outbox.publish(
            CalendarShareFailed(
                actor_id=actor_id,
                user_id=user_id,
                error_class=type(exc).__name__,
            ).as_envelope()
        )
        log.warning(
            "calendar_share.failed",
            user_id=str(user_id),
            user_email=user_email,
            error_class=type(exc).__name__,
        )
        # Do NOT re-raise — subscription itself succeeded; share is best-effort.
        # Admin sees the failure status in UI and can use the retry endpoint.


async def _attempt_revoke(
    *,
    db: Any,
    actor_id: uuid.UUID,
    user_id: uuid.UUID,
    user_email: str,
) -> None:
    """Try EWS revoke and update share_status.  Never raises."""
    repo = SqlaCalendarSubscriptionRepository(db)
    outbox = SqlaEventOutbox(db)

    ews_config = await _load_ews_config(db)

    if ews_config is None or not user_email:
        # Channel absent or no email — silently skip revoke; subscription is
        # disabled in DB regardless.
        log.info(
            "calendar_share.revoke_skipped",
            user_id=str(user_id),
            reason="channel absent or no email",
        )
        await repo.set_share_status(
            user_id=user_id,
            share_status="not_attempted",
            share_error=None,
        )
        return

    try:
        await asyncio.to_thread(
            revoke_calendar_share,
            ews_config=ews_config,
            user_email=user_email,
        )
        await repo.set_share_status(
            user_id=user_id,
            share_status="revoked",
            share_error=None,
        )
        await outbox.publish(
            CalendarShareRevoked(
                actor_id=actor_id,
                user_id=user_id,
                user_email=user_email,
            ).as_envelope()
        )
        log.info(
            "calendar_share.revoked",
            user_id=str(user_id),
            user_email=user_email,
        )
    except Exception as exc:
        err_msg = str(exc) if isinstance(exc, EwsShareError) else f"EWS error: {type(exc).__name__}"
        await repo.set_share_status(
            user_id=user_id,
            share_status="failed",
            share_error=f"revoke failed: {err_msg}",
        )
        log.warning(
            "calendar_share.revoke_failed",
            user_id=str(user_id),
            user_email=user_email,
            error_class=type(exc).__name__,
        )
        # Do NOT re-raise — subscription disable happened in DB.


def _row_to_response(row: Any) -> SubscriptionResponse:
    return SubscriptionResponse(
        user_id=row.user_id,
        enabled=row.enabled,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        share_status=row.share_status,
        share_granted_at=row.share_granted_at,
        share_error=row.share_error,
        ics_feed_token=getattr(row, "ics_feed_token", None),
    )


# ---------------------------------------------------------------------------
# GET /admin/calendar-subscriptions
# ---------------------------------------------------------------------------


@router.get("", response_model=list[SubscriptionResponse])
async def list_subscriptions(
    actor: RequireAdmin,
    db: DbSession,
) -> list[SubscriptionResponse]:
    """List all calendar subscriptions including share_status."""
    async with db.begin():
        repo = SqlaCalendarSubscriptionRepository(db)
        rows = await repo.list_all()

    return [_row_to_response(row) for row in rows]


# ---------------------------------------------------------------------------
# POST /admin/calendar-subscriptions
# ---------------------------------------------------------------------------


@router.post("", status_code=201, response_model=SubscriptionResponse)
async def add_subscription(
    body: AddSubscriptionRequest,
    actor: RequireAdmin,
    db: DbSession,
    settings: AppSettings,
) -> SubscriptionResponse:
    """Add or re-enable a user in the calendar subscription whitelist.

    Automatically grants EWS Reviewer permission (ADR-0005 §7).
    share_status reflects the outcome of the EWS call:
      - 'granted'       — EWS permission set succeeded
      - 'failed'        — EWS call failed (see share_error for details)
      - 'not_attempted' — exchange_calendar channel not configured / no email

    Requires re-MFA (enforced at BFF).
    """
    user_email = body.user_email.strip()

    # Phase 1: INSERT subscription row with share_status='pending'.
    async with db.begin():
        repo = SqlaCalendarSubscriptionRepository(db)
        await repo.upsert(
            user_id=body.user_id,
            enabled=True,
            created_by=actor.actor_id,
            share_status="pending",
        )

    log.info(
        "admin.calendar_subscription_added",
        user_id=str(body.user_id),
        actor_id=str(actor.actor_id),
    )

    # Phase 2: attempt EWS grant (separate transaction so the row is visible).
    async with db.begin():
        await _attempt_grant(
            db=db,
            actor_id=actor.actor_id,
            user_id=body.user_id,
            user_email=user_email,
        )
        repo = SqlaCalendarSubscriptionRepository(db)
        row = await repo.get(body.user_id)

    if row is None:
        raise HTTPException(status_code=500, detail="Subscription row not found after insert")
    return _row_to_response(row)


# ---------------------------------------------------------------------------
# DELETE /admin/calendar-subscriptions/{user_id}
# ---------------------------------------------------------------------------


@router.delete("/{user_id}", status_code=200, response_model=SubscriptionResponse)
async def remove_subscription(
    user_id: uuid.UUID,
    body: dict[str, str],
    actor: RequireAdmin,
    db: DbSession,
    settings: AppSettings,
) -> SubscriptionResponse:
    """Soft-disable a user in the calendar subscription whitelist.

    Automatically revokes EWS Reviewer permission (ADR-0005 §7).
    Sets enabled=false (preserves audit history per ADR-0005 §3).
    Requires re-MFA (enforced at BFF).
    """
    user_email = body.get("user_email", "").strip()

    async with db.begin():
        repo = SqlaCalendarSubscriptionRepository(db)
        existing = await repo.get(user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        # Disable the subscription.
        await repo.upsert(
            user_id=user_id,
            enabled=False,
            created_by=existing.created_by,
            share_status="pending",
        )

    log.info(
        "admin.calendar_subscription_removed",
        user_id=str(user_id),
        actor_id=str(actor.actor_id),
    )

    # Attempt EWS revoke in a separate transaction.
    async with db.begin():
        await _attempt_revoke(
            db=db,
            actor_id=actor.actor_id,
            user_id=user_id,
            user_email=user_email,
        )
        repo = SqlaCalendarSubscriptionRepository(db)
        row = await repo.get(user_id)

    if row is None:
        raise HTTPException(status_code=500, detail="Subscription row not found after update")
    return _row_to_response(row)


# ---------------------------------------------------------------------------
# POST /admin/calendar-subscriptions/{user_id}/retry-share
# ---------------------------------------------------------------------------


@router.post("/{user_id}/retry-share", status_code=200, response_model=SubscriptionResponse)
async def retry_share(
    user_id: uuid.UUID,
    body: dict[str, str],
    actor: RequireAdmin,
    db: DbSession,
    settings: AppSettings,
) -> SubscriptionResponse:
    """Retry EWS calendar share grant for a failed or not_attempted subscription.

    Useful when:
    - exchange_calendar channel was configured AFTER the subscription was created
    - A transient EWS error caused the initial grant to fail

    Requires re-MFA (enforced at BFF).
    Body: {user_email: str}  — required for the EWS call.
    """
    user_email = body.get("user_email", "").strip()
    if not user_email:
        raise HTTPException(
            status_code=422,
            detail="user_email is required in body for retry-share",
        )

    async with db.begin():
        repo = SqlaCalendarSubscriptionRepository(db)
        existing = await repo.get(user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        if not existing.enabled:
            raise HTTPException(
                status_code=409,
                detail="Cannot retry share for a disabled subscription",
            )
        # Mark as pending before attempting.
        await repo.set_share_status(
            user_id=user_id,
            share_status="pending",
            share_error=None,
        )

    log.info(
        "admin.calendar_subscription_retry_share",
        user_id=str(user_id),
        actor_id=str(actor.actor_id),
    )

    async with db.begin():
        await _attempt_grant(
            db=db,
            actor_id=actor.actor_id,
            user_id=user_id,
            user_email=user_email,
        )
        repo = SqlaCalendarSubscriptionRepository(db)
        row = await repo.get(user_id)

    if row is None:
        raise HTTPException(status_code=500, detail="Subscription row not found after retry")
    return _row_to_response(row)


# ---------------------------------------------------------------------------
# POST /admin/calendar-subscriptions/{user_id}/mark-granted
# ---------------------------------------------------------------------------


@router.post("/{user_id}/mark-granted", status_code=200, response_model=SubscriptionResponse)
async def mark_granted(
    user_id: uuid.UUID,
    actor: RequireAdmin,
    db: DbSession,
) -> SubscriptionResponse:
    """Manually flip share_status to 'granted'.

    Use case: corp Exchange refuses EWS PermissionSet writes (a known quirk
    of some on-prem deployments) but IT granted Reviewer to the subscriber
    via PowerShell `Add-MailboxFolderPermission`. Lotsman has no way to
    auto-detect that, so admin clicks this to record the fact.

    Audit-event records the manual override (so it's traceable later).
    """
    async with db.begin():
        repo = SqlaCalendarSubscriptionRepository(db)
        existing = await repo.get(user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        if not existing.enabled:
            raise HTTPException(
                status_code=409,
                detail="Cannot mark a disabled subscription as granted",
            )
        await repo.set_share_status(
            user_id=user_id,
            share_status="granted",
            share_error=None,
        )
        outbox = SqlaEventOutbox(db)
        from notification_service.domain.events import CalendarShareGranted
        await outbox.publish(
            CalendarShareGranted(
                actor_id=actor.actor_id,
                user_id=user_id,
            ).as_envelope()
        )

        row = await repo.get(user_id)

    log.info(
        "admin.calendar_subscription_marked_granted",
        user_id=str(user_id),
        actor_id=str(actor.actor_id),
    )

    if row is None:
        raise HTTPException(status_code=500, detail="Subscription row missing after update")
    return _row_to_response(row)
