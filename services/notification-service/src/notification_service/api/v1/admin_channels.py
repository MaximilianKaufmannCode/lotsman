# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Admin channel management endpoints — /api/v1/admin/channels/*

Re-MFA is enforced at the BFF layer BEFORE these endpoints are called.
notification-service trusts the internal JWT from web-bff and does NOT
re-verify the TOTP code — this is by design (see spec §Re-MFA enforcement).

Endpoints:
  GET    /admin/channels                  — list channels (no re-MFA needed)
  PUT    /admin/channels/{channel}        — set full config (re-MFA at BFF)
  PATCH  /admin/channels/{channel}        — partial update: enabled flag (re-MFA at BFF)
  POST   /admin/channels/{channel}/test   — send test message (re-MFA at BFF)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from lotsman_shared.actors import ACTOR_SYSTEM_MIGRATOR
from lotsman_shared.internal_jwt import InternalJWTClaims
from pydantic import BaseModel

from notification_service.api.deps import DbSession, current_actor
from notification_service.application.use_cases.disable_channel import DisableChannel
from notification_service.application.use_cases.get_channel_config import GetChannelConfig
from notification_service.application.use_cases.get_channels import GetChannels
from notification_service.application.use_cases.set_channel_config import SetChannelConfig
from notification_service.application.use_cases.test_channel import TestChannel
from notification_service.domain.channels import Channel, ExchangeCalendarConfig
from notification_service.domain.errors import (
    ChannelDecryptError,
    ChannelNotConfiguredError,
    ChannelNotImplementedError,
    ChannelValidationError,
    PendingInvitationsError,
)
from notification_service.domain.events import ChannelChanged
from notification_service.infrastructure.channel_crypto import ChannelCipher
from notification_service.infrastructure.db.repositories import (
    SqlaCredentialRepository,
    SqlaEventOutbox,
)
from notification_service.infrastructure.redis.invite_store import RedisInviteStore

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/channels", tags=["admin-channels"])

_VALID_CHANNELS: set[str] = {"email", "telegram", "dion", "exchange_calendar", "ics_feed"}

# Module-level cipher singleton — constructed once at import time.
# Raises RuntimeError on import if CHANNEL_ENC_KEY is missing.
_cipher = ChannelCipher()


def _get_invite_store(request: Request) -> RedisInviteStore:
    """Retrieve the RedisInviteStore backed by app.state.redis (wired in lifespan).

    F-009 / Blocker 4: the previous _NoopInviteStore always returned False, making
    the pending-invites pre-check dead code.  Now wired to the real Redis adapter.
    """
    return RedisInviteStore(request.app.state.redis)


def _require_admin(
    actor: Annotated[InternalJWTClaims | None, Depends(current_actor)],
) -> InternalJWTClaims:
    if actor is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if actor.role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    return actor


RequireAdmin = Annotated[InternalJWTClaims, Depends(_require_admin)]


def _validate_channel(channel: str) -> Channel:
    if channel not in _VALID_CHANNELS:
        raise HTTPException(status_code=404, detail=f"Unknown channel: {channel}")
    return channel  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class SetChannelRequest(BaseModel):
    enabled: bool
    config: dict[str, Any]


class PatchChannelRequest(BaseModel):
    enabled: bool


class ChannelStatusResponse(BaseModel):
    channel: str
    enabled: bool
    configured: bool
    updated_at: datetime | None
    status: str


class TestChannelRequest(BaseModel):
    recipient: str | None = None  # defaults to actor's email (passed by BFF)


class TestChannelResponse(BaseModel):
    queued: bool
    destination: str
    test_id: uuid.UUID
    # Email channel: which transport actually delivered ("smtp" or "ews"
    # — EWS used as fallback when corp Exchange blocks SMTP Send-As).
    # None for non-email channels.
    transport: str | None = None


class ChannelConfigResponse(BaseModel):
    channel: str
    config: dict[str, Any]  # secret fields replaced with "********"


# ---------------------------------------------------------------------------
# GET /admin/channels
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ChannelStatusResponse])
async def list_channels(
    actor: RequireAdmin,
    db: DbSession,
) -> list[ChannelStatusResponse]:
    """List all channel statuses. Never returns decrypted configs."""
    async with db.begin():
        use_case = GetChannels(
            credential_repo=SqlaCredentialRepository(db),
            cipher=_cipher,
        )
        statuses = await use_case.execute()

    return [
        ChannelStatusResponse(
            channel=s.channel,
            enabled=s.enabled,
            configured=s.configured,
            updated_at=s.updated_at,
            status=s.status,
        )
        for s in statuses
    ]


# ---------------------------------------------------------------------------
# GET /admin/channels/{channel}/config
# ---------------------------------------------------------------------------


@router.get("/{channel}/config", response_model=ChannelConfigResponse)
async def get_channel_config(
    channel: str,
    actor: RequireAdmin,
    db: DbSession,
) -> ChannelConfigResponse:
    """Return current decrypted config for a channel with secrets masked as '********'.

    No re-MFA required (read-only, same security level as GET /admin/channels).
    404 if the channel has no stored config.
    502 if stored config cannot be decrypted (key rotation issue).
    """
    ch = _validate_channel(channel)

    try:
        async with db.begin():
            use_case = GetChannelConfig(
                credential_repo=SqlaCredentialRepository(db),
                cipher=_cipher,
            )
            config = await use_case.execute(channel=ch)
    except ChannelNotConfiguredError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except ChannelDecryptError as exc:
        raise HTTPException(
            status_code=502,
            detail={"detail": exc.message, "code": "CHANNEL_DECRYPT_ERROR"},
        ) from exc

    log.info(
        "admin.channel_config_read",
        channel=channel,
        actor_id=str(actor.actor_id),
    )
    return ChannelConfigResponse(channel=ch, config=config)


# ---------------------------------------------------------------------------
# PUT /admin/channels/{channel}
# ---------------------------------------------------------------------------


@router.put("/{channel}", status_code=200)
async def set_channel_config(
    channel: str,
    body: SetChannelRequest,
    actor: RequireAdmin,
    db: DbSession,
) -> dict[str, str]:
    """Set or replace the full config for a channel. Requires re-MFA (enforced at BFF)."""
    ch = _validate_channel(channel)

    try:
        async with db.begin():
            use_case = SetChannelConfig(
                credential_repo=SqlaCredentialRepository(db),
                outbox=SqlaEventOutbox(db),
                cipher=_cipher,
            )
            await use_case.execute(
                actor_id=actor.actor_id,
                channel=ch,
                config=body.config,
                enabled=body.enabled,
            )
    except ChannelValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc

    log.info(
        "admin.channel_configured",
        channel=channel,
        actor_id=str(actor.actor_id),
    )
    return {"detail": "Channel configured"}


# ---------------------------------------------------------------------------
# PATCH /admin/channels/{channel}
# ---------------------------------------------------------------------------


@router.patch("/{channel}", status_code=200)
async def patch_channel(
    channel: str,
    body: PatchChannelRequest,
    actor: RequireAdmin,
    db: DbSession,
    request: Request,
) -> dict[str, str]:
    """Toggle enabled flag for a channel. Requires re-MFA (enforced at BFF)."""
    ch = _validate_channel(channel)

    if not body.enabled:
        try:
            async with db.begin():
                use_case = DisableChannel(
                    credential_repo=SqlaCredentialRepository(db),
                    invite_store=_get_invite_store(request),
                    outbox=SqlaEventOutbox(db),
                )
                await use_case.execute(actor_id=actor.actor_id, channel=ch)
        except PendingInvitationsError as exc:
            raise HTTPException(status_code=409, detail=exc.message) from exc
    else:
        # Enable: just flip the flag and emit hot-reload signal.
        async with db.begin():
            outbox = SqlaEventOutbox(db)
            repo = SqlaCredentialRepository(db)
            await repo.set_enabled(channel=ch, enabled=True)
            await outbox.publish(
                ChannelChanged(
                    system_actor_id=ACTOR_SYSTEM_MIGRATOR,
                    channel=ch,
                ).as_envelope()
            )

    log.info(
        "admin.channel_patched",
        channel=channel,
        enabled=body.enabled,
        actor_id=str(actor.actor_id),
    )
    return {"detail": "Channel updated"}


# ---------------------------------------------------------------------------
# POST /admin/channels/{channel}/test
# ---------------------------------------------------------------------------


class ExchangeTestResponse(BaseModel):
    success: bool
    detail: str
    latency_ms: float | None = None


@router.post("/exchange_calendar/test", status_code=200)
async def test_exchange_calendar(
    actor: RequireAdmin,
    db: DbSession,
) -> ExchangeTestResponse:
    """Test Exchange Calendar connectivity by creating and deleting a probe event.

    Requires re-MFA (enforced at BFF).
    ics_feed has no test endpoint — UI shows the URL after save instead.
    """
    async with db.begin():
        repo = SqlaCredentialRepository(db)
        rows = await repo.get_all()

    ews_row = next((r for r in rows if r.channel == "exchange_calendar"), None)
    if ews_row is None:
        raise HTTPException(status_code=404, detail="exchange_calendar channel not configured")

    try:
        config_dict = _cipher.decrypt(ews_row.config_enc)
        ews_config = ExchangeCalendarConfig(**config_dict)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to decrypt channel config") from exc

    # Import driver lazily — avoids failing at module-load time if exchangelib unavailable.
    from notification_service.infrastructure.calendar.ews_driver import EwsCalendarDriver

    driver = EwsCalendarDriver(ews_config)
    result = await driver.test_connection(mailbox=ews_config.target_mailbox)

    log.info(
        "admin.exchange_calendar_tested",
        success=result.success,
        actor_id=str(actor.actor_id),
    )
    return ExchangeTestResponse(
        success=result.success,
        detail=result.detail,
        latency_ms=result.latency_ms,
    )


@router.post("/{channel}/test", status_code=202)
async def test_channel(
    channel: str,
    body: TestChannelRequest,
    actor: RequireAdmin,
    db: DbSession,
) -> TestChannelResponse:
    """Send a test message. Requires re-MFA (enforced at BFF).

    Telegram, Dion, and ics_feed return 501 — not implemented in Phase 2b.
    exchange_calendar uses its own dedicated endpoint above.
    """
    ch = _validate_channel(channel)

    if ch == "ics_feed":
        raise HTTPException(
            status_code=501,
            detail=(
                "ICS feed has no test endpoint — "
                "configure and copy the feed URL from the channel page."
            ),
        )

    recipient = body.recipient or ""
    if not recipient:
        raise HTTPException(
            status_code=400,
            detail="recipient is required — BFF must pass admin email in body",
        )

    try:
        async with db.begin():
            use_case = TestChannel(
                credential_repo=SqlaCredentialRepository(db),
                outbox=SqlaEventOutbox(db),
                cipher=_cipher,
            )
            result = await use_case.execute(
                actor_id=actor.actor_id,
                channel=ch,
                recipient=recipient,
            )
    except ChannelNotImplementedError as exc:
        raise HTTPException(status_code=501, detail=exc.message) from exc

    return TestChannelResponse(
        queued=True,
        destination=str(result["destination"]),
        test_id=uuid.UUID(str(result["test_id"])),
        transport=result.get("transport"),
    )
