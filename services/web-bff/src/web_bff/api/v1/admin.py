# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Admin proxy routes — /api/v1/admin/*

Proxies admin endpoints from `web/src/pages/admin/users/api.ts` to auth-service.
Every route gates on `require_admin` BEFORE the round-trip — non-admins receive
a fast 403 without touching auth-service.

Sensitive admin actions (password reset, TOTP reset) require the admin to have
re-MFA'd recently. The admin's re-MFA flag is enforced server-side by auth-service
via its Redis mfa-verified flag (set by /api/v1/auth/re-mfa); the BFF forwards
the call and trusts upstream's check.

Path mappings (SPA → auth-service):
  GET    /api/v1/admin/users                          → /api/v1/admin/users
  POST   /api/v1/admin/users                          → /api/v1/admin/users
  GET    /api/v1/admin/users/{id}                     → /api/v1/admin/users/{id}
  PATCH  /api/v1/admin/users/{id}  → /api/v1/admin/users/{id}/role (role-only)
                                  OR /api/v1/admin/users/{id}/deactivate (active=false)
  POST   /api/v1/admin/users/{id}/lockout             → POST /api/v1/admin/users/{id}/lockout
  DELETE /api/v1/admin/users/{id}/lockout             → DELETE /api/v1/admin/users/{id}/lockout
  GET    /api/v1/admin/users/{id}/sessions            → /api/v1/admin/users/{id}/sessions
  DELETE /api/v1/admin/users/{id}/sessions            → /api/v1/admin/users/{id}/sessions
  POST   /api/v1/admin/users/{id}/totp/reset          → /api/v1/admin/users/{id}/totp/reset
  POST   /api/v1/admin/users/{id}/password/reset      → /api/v1/admin/users/{id}/password/reset
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile

from web_bff.api.deps import (
    GetAuthClient,
    GetNotificationClient,
    GetRegistryClient,
    RequireAdmin,
    get_request_id,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _upstream_error(upstream_resp: Any) -> HTTPException:
    """Convert a non-2xx upstream response into an HTTPException, preserving detail + code.

    Blocker 5: propagate the upstream ``code`` field so the SPA can map errors
    to specific UX states (PENDING_INVITES, MIN_ADMINS, NO_CHANNEL, etc.).
    The ``code`` is forwarded as a custom response header AND included in the
    JSON body so ``apiFetch`` can read it from either location.
    """
    try:
        body = upstream_resp.json()
        detail = body.get("detail", "Upstream error")
        code = body.get("code")
    except Exception:
        detail = "Upstream error"
        code = None
    headers: dict[str, str] = {}
    if code:
        headers["X-Error-Code"] = code
    return HTTPException(
        status_code=upstream_resp.status_code,
        detail={"detail": detail, "code": code} if code else detail,
        headers=headers if headers else None,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/admin/users
# ---------------------------------------------------------------------------


@router.get("/users")
async def list_users(
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """List all users (admin-only)."""
    upstream = await auth_client.admin_list_users(
        actor_id=admin.subject,
        role=admin.role,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# GET /api/v1/admin/users/{user_id}
# ---------------------------------------------------------------------------


@router.get("/users/{user_id}")
async def get_user(
    user_id: uuid.UUID,
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Get a single user by ID (admin-only)."""
    upstream = await auth_client.admin_get_user(
        actor_id=admin.subject,
        role=admin.role,
        user_id=user_id,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# POST /api/v1/admin/users
# Body: {email, full_name, role}
# Returns: {user_id, oob_otp} — OTP shown ONCE in the SPA modal.
# ---------------------------------------------------------------------------


@router.post("/users", status_code=201)
async def create_user(
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Create a new user (admin-only).

    Requires re-MFA (BFF is the sole chokepoint per ADR-0004 §6).
    """
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    upstream = await auth_client.admin_create_user(
        actor_id=admin.subject,
        role=admin.role,
        body=body,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# PATCH /api/v1/admin/users/{user_id}
# Body: {role?: "admin"|"editor"|"viewer", active?: bool}
# The SPA sends a partial PATCH; this proxy fans out to the appropriate
# auth-service endpoints (role change vs. deactivation).
# ---------------------------------------------------------------------------


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: uuid.UUID,
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Patch user fields (role change / deactivation). Requires re-MFA per ADR-0004 §6."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    new_role = body.get("role")
    active = body.get("active")

    last_response: Any = None

    if new_role is not None:
        upstream = await auth_client.admin_change_role(
            actor_id=admin.subject,
            role=admin.role,
            user_id=user_id,
            new_role=str(new_role),
            request_id=request_id,
        )
        if not upstream.is_success:
            raise _upstream_error(upstream)
        last_response = upstream

    if active is False:
        upstream = await auth_client.admin_deactivate_user(
            actor_id=admin.subject,
            role=admin.role,
            user_id=user_id,
            request_id=request_id,
        )
        if not upstream.is_success:
            raise _upstream_error(upstream)
        last_response = upstream

    if active is True:
        # US-104: reactivate a previously soft-deleted user.
        upstream = await auth_client.admin_reactivate_user(
            actor_id=admin.subject,
            role=admin.role,
            user_id=user_id,
            request_id=request_id,
        )
        if not upstream.is_success:
            raise _upstream_error(upstream)
        last_response = upstream

    full_name = body.get("full_name")
    if isinstance(full_name, str) and full_name.strip():
        # US-103: admin updates target user's profile (currently only full_name).
        upstream = await auth_client.admin_update_user_profile(
            actor_id=admin.subject,
            role=admin.role,
            user_id=user_id,
            full_name=full_name,
            request_id=request_id,
        )
        if not upstream.is_success:
            raise _upstream_error(upstream)
        last_response = upstream

    if last_response is None:
        raise HTTPException(status_code=400, detail="No supported fields in patch body")

    if last_response.status_code == 204 or not last_response.content:
        return {"detail": "Updated"}
    return last_response.json()


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/users/{user_id} — permanent (soft) delete. Requires re-MFA.
# ---------------------------------------------------------------------------


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> None:
    """Permanently soft-delete a user (hide + free email). Re-MFA per ADR-0004 §6."""
    await _verify_re_mfa(
        admin=admin,
        totp_code=body.get("totp_code"),
        auth_client=auth_client,
        request_id=request_id,
    )
    upstream = await auth_client.admin_delete_user(
        actor_id=admin.subject,
        role=admin.role,
        user_id=user_id,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)


# ---------------------------------------------------------------------------
# POST /api/v1/admin/users/{user_id}/lockout
# DELETE /api/v1/admin/users/{user_id}/lockout
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/lockout", status_code=204)
async def lock_user(
    user_id: uuid.UUID,
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> None:
    """Instant kill-switch (US-13). Requires re-MFA per ADR-0004 §6."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    upstream = await auth_client.admin_lock_user(
        actor_id=admin.subject,
        role=admin.role,
        user_id=user_id,
        request_id=request_id,
    )
    if not upstream.is_success and upstream.status_code != 204:
        raise _upstream_error(upstream)


@router.delete("/users/{user_id}/lockout", status_code=204)
async def unlock_user(
    user_id: uuid.UUID,
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> None:
    """Manual unlock (US-13). Requires re-MFA per ADR-0004 §6."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    upstream = await auth_client.admin_unlock_user(
        actor_id=admin.subject,
        role=admin.role,
        user_id=user_id,
        request_id=request_id,
    )
    if not upstream.is_success and upstream.status_code != 204:
        raise _upstream_error(upstream)


# ---------------------------------------------------------------------------
# GET /api/v1/admin/users/{user_id}/sessions
# DELETE /api/v1/admin/users/{user_id}/sessions
# ---------------------------------------------------------------------------


@router.get("/users/{user_id}/sessions")
async def list_user_sessions(
    user_id: uuid.UUID,
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """List all sessions of a target user (US-21)."""
    upstream = await auth_client.admin_get_user_sessions(
        actor_id=admin.subject,
        role=admin.role,
        user_id=user_id,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.delete("/users/{user_id}/sessions", status_code=204)
async def revoke_all_user_sessions(
    user_id: uuid.UUID,
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> None:
    """Revoke all sessions of a target user (US-15). Requires re-MFA per ADR-0004 §6."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    upstream = await auth_client.admin_revoke_all_sessions(
        actor_id=admin.subject,
        role=admin.role,
        user_id=user_id,
        request_id=request_id,
    )
    if not upstream.is_success and upstream.status_code != 204:
        raise _upstream_error(upstream)


@router.delete("/users/{user_id}/sessions/{session_id}", status_code=204)
async def revoke_user_session(
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> None:
    """Revoke ONE specific session of a target user (US-105). Re-MFA gated per ADR-0004 §6."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    upstream = await auth_client.admin_revoke_session(
        actor_id=admin.subject,
        role=admin.role,
        user_id=user_id,
        session_id=session_id,
        request_id=request_id,
    )
    if not upstream.is_success and upstream.status_code != 204:
        raise _upstream_error(upstream)


# ---------------------------------------------------------------------------
# POST /api/v1/admin/users/{user_id}/totp/reset
# Body: {admin_totp_code} — admin re-MFA per ADR-0003 §5
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/totp/reset")
async def reset_target_totp(
    user_id: uuid.UUID,
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Reset a target user's TOTP (US-16). Requires re-MFA per ADR-0004 §6.

    The BFF verifies the TOTP code here and strips it before forwarding.
    auth-service uses its inline TOTP verification for this endpoint (admin_totp_code field).
    Both checks use the same TOTP code supplied by the admin.
    """
    # Use the same totp_code for both the BFF re-MFA gate and the auth-service inline check.
    totp_code = body.get("totp_code") or body.get("admin_totp_code", "")
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code or None,
        auth_client=auth_client,
        request_id=request_id,
    )
    # auth-service reset_user_totp uses admin_totp_code for its own inline re-MFA.
    admin_totp_code = body.get("admin_totp_code", totp_code)
    body.pop("totp_code", None)
    if not admin_totp_code:
        raise HTTPException(status_code=400, detail="admin_totp_code required")
    body["admin_totp_code"] = admin_totp_code

    upstream = await auth_client.admin_reset_totp(
        actor_id=admin.subject,
        role=admin.role,
        user_id=user_id,
        admin_totp_code=admin_totp_code,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json() if upstream.content else {"detail": "TOTP reset"}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/users/{user_id}/password/reset
# Returns: {oob_otp} — admin relays this to the user out of band.
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/password/reset")
async def reset_target_password(
    user_id: uuid.UUID,
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Reset a target user's password (US-7 admin path). Requires re-MFA per ADR-0004 §6."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    upstream = await auth_client.admin_reset_password(
        actor_id=admin.subject,
        role=admin.role,
        user_id=user_id,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# POST /api/v1/admin/users/{user_id}/invite — re-invite pending user (US-10)
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/invite")
async def re_invite_user(
    user_id: uuid.UUID,
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Re-invite a pending user. Requires re-MFA per ADR-0004 §6."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    upstream = await auth_client.admin_re_invite_user(
        actor_id=admin.subject,
        role=admin.role,
        user_id=user_id,
        body=body,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# Admin channel proxy routes (ADR-0004 Phase 2b)
#
# Re-MFA flow at BFF:
#   1. Extract totp_code from request body.
#   2. Call auth-service /api/v1/auth/mfa-check to validate.
#   3. If valid: forward to notification-svc with internal JWT (no totp_code).
#   4. notification-svc trusts the internal JWT, never sees the TOTP code.
# ---------------------------------------------------------------------------


async def _verify_re_mfa(
    *,
    admin: Any,
    totp_code: str | None,
    auth_client: Any,
    request_id: str | None,
) -> None:
    """Call auth-service re-MFA check. Raises 401 if TOTP code is missing or invalid.

    BFF is the sole MFA chokepoint per ADR-0004 §6.  Propagates ``code`` from
    upstream so the SPA can distinguish REMFA_REQUIRED from REMFA_REPLAY.
    """
    if not totp_code:
        raise HTTPException(
            status_code=401,
            detail={
                "detail": "TOTP code required for sensitive operations",
                "code": "REMFA_REQUIRED",
            },
            headers={"X-Error-Code": "REMFA_REQUIRED"},
        )
    resp = await auth_client.re_mfa_check(
        actor_id=admin.subject,
        role=admin.role,
        totp_code=totp_code,
        request_id=request_id,
    )
    if not resp.is_success:
        try:
            body = resp.json()
            detail = body.get("detail", "Re-authentication failed")
            code = body.get("code", "REMFA_REQUIRED")
        except Exception:
            detail = "Re-authentication failed"
            code = "REMFA_REQUIRED"
        raise HTTPException(
            status_code=resp.status_code,
            detail={"detail": detail, "code": code},
            headers={"X-Error-Code": code},
        )


@router.get("/channels")
async def list_channels(
    admin: RequireAdmin,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """List channel statuses (admin-only, no re-MFA needed for read). US-2.1."""
    upstream = await notification_client.admin_list_channels(
        actor_id=admin.subject,
        role=admin.role,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.get("/channels/{channel}/config")
async def get_channel_config(
    channel: str,
    admin: RequireAdmin,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Return current channel config with secrets masked as '********' (admin-only, no re-MFA).

    Enables edit-dialog pre-population in the SPA. Secret fields are replaced
    with the literal '********' — the frontend must forward this value unchanged
    when the admin does not edit a secret field, so the backend preserves the
    existing stored secret.
    """
    upstream = await notification_client.admin_get_channel_config(
        actor_id=admin.subject,
        role=admin.role,
        channel=channel,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.put("/channels/{channel}")
async def set_channel(
    channel: str,
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Set channel config (admin + re-MFA). US-2, US-3, US-4."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    upstream = await notification_client.admin_set_channel(
        actor_id=admin.subject,
        role=admin.role,
        channel=channel,
        body=body,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json() if upstream.content else {"detail": "Channel configured"}


@router.patch("/channels/{channel}")
async def patch_channel(
    channel: str,
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Partial channel update (admin + re-MFA). US-6."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    upstream = await notification_client.admin_patch_channel(
        actor_id=admin.subject,
        role=admin.role,
        channel=channel,
        body=body,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json() if upstream.content else {"detail": "Channel updated"}


@router.post("/channels/exchange_calendar/test", status_code=200)
async def test_exchange_calendar_channel(
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Test Exchange Calendar connectivity (admin + re-MFA). ADR-0005 §6."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    upstream = await notification_client.admin_test_exchange_calendar(
        actor_id=admin.subject,
        role=admin.role,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.post("/channels/{channel}/test", status_code=202)
async def test_channel(
    channel: str,
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Test channel (admin + re-MFA). US-5."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    # Resolve fresh admin email if recipient not in body. JWT claim can be
    # stale after a self-service email change (claim is baked at login + only
    # refreshed on next refresh-token rotation, max 15 min lag). Hit auth-svc
    # /auth/me for the current value so test always lands on the actual inbox.
    if "recipient" not in body:
        try:
            me_resp = await auth_client.get_my_profile(
                actor_id=admin.subject,
                role=admin.role,
                request_id=request_id,
            )
            if me_resp.is_success:
                fresh_email = me_resp.json().get("email")
                if fresh_email:
                    body["recipient"] = fresh_email
        except Exception:
            pass  # network blip — fall back to JWT claim below
        if "recipient" not in body:
            body["recipient"] = admin.email
    try:
        upstream = await notification_client.admin_test_channel(
            actor_id=admin.subject,
            role=admin.role,
            channel=channel,
            body=body,
            request_id=request_id,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                f"Тест канала '{channel}' превысил таймаут (45 сек). "
                f"Скорее всего сервер недоступен или неверно настроен host/port. "
                f"Проверьте параметры подключения."
            ),
        ) from exc
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# Admin calendar subscription proxy routes (ADR-0005 §3)
#
# Re-MFA is required for add/remove. List is read-only (no re-MFA).
# ---------------------------------------------------------------------------


@router.get("/calendar-subscriptions")
async def list_calendar_subscriptions(
    admin: RequireAdmin,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """List calendar subscription whitelist (admin-only, no re-MFA). ADR-0005 §3."""
    upstream = await notification_client.admin_list_calendar_subscriptions(
        actor_id=admin.subject,
        role=admin.role,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.post("/calendar-subscriptions", status_code=201)
async def add_calendar_subscription(
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Add user to calendar subscription whitelist (admin + re-MFA). ADR-0005 §3."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    # Resolve user_email from auth-svc (notification-svc needs it to call EWS
    # permission_set against the user's actual mailbox). SPA only sends user_id.
    target_user_id = body.get("user_id")
    if target_user_id and "user_email" not in body:
        try:
            user_resp = await auth_client.admin_get_user(
                actor_id=admin.subject,
                role=admin.role,
                user_id=uuid.UUID(str(target_user_id)),
                request_id=request_id,
            )
            if user_resp.is_success:
                body["user_email"] = user_resp.json().get("email", "")
        except Exception:
            pass  # leave empty — notification-svc will record share_status='not_attempted'
    upstream = await notification_client.admin_add_calendar_subscription(
        actor_id=admin.subject,
        role=admin.role,
        body=body,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json() if upstream.content else {"detail": "Subscription added"}


@router.delete("/calendar-subscriptions/{user_id}", status_code=200)
async def remove_calendar_subscription(
    user_id: uuid.UUID,
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Soft-disable user in calendar subscription whitelist (admin + re-MFA). ADR-0005 §3."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    # Resolve user_email if SPA didn't send it — same fix as add_calendar_subscription.
    user_email = body.get("user_email", "")
    if not user_email:
        try:
            user_resp = await auth_client.admin_get_user(
                actor_id=admin.subject,
                role=admin.role,
                user_id=user_id,
                request_id=request_id,
            )
            if user_resp.is_success:
                user_email = user_resp.json().get("email", "")
        except Exception:
            pass
    upstream = await notification_client.admin_remove_calendar_subscription(
        actor_id=admin.subject,
        role=admin.role,
        user_id=user_id,
        body={"user_email": user_email},
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json() if upstream.content else {"detail": "Subscription disabled"}


@router.post("/calendar-subscriptions/{user_id}/retry-share", status_code=200)
async def retry_calendar_share(
    user_id: uuid.UUID,
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Retry EWS calendar share grant (admin + re-MFA). ADR-0005 §7."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    # Resolve user_email from auth-svc if SPA didn't send it.
    if "user_email" not in body or not body.get("user_email"):
        try:
            user_resp = await auth_client.admin_get_user(
                actor_id=admin.subject,
                role=admin.role,
                user_id=user_id,
                request_id=request_id,
            )
            if user_resp.is_success:
                body["user_email"] = user_resp.json().get("email", "")
        except Exception:
            pass
    try:
        upstream = await notification_client.admin_retry_calendar_share(
            actor_id=admin.subject,
            role=admin.role,
            user_id=user_id,
            body=body,
            request_id=request_id,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                "Retry-share превысил таймаут (45 сек). "
                "Exchange недоступен или не отвечает. Попробуйте позже."
            ),
        ) from exc
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.post("/calendar-subscriptions/{user_id}/mark-granted", status_code=200)
async def mark_calendar_share_granted(
    user_id: uuid.UUID,
    admin: RequireAdmin,
    notification_client: GetNotificationClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Manually mark calendar share as granted (admin-only, no re-MFA — pure DB flip).

    Used after IT grants Reviewer via PowerShell out-of-band when EWS
    PermissionSet writes don't go through.
    """
    upstream = await notification_client.admin_mark_calendar_share_granted(
        actor_id=admin.subject,
        role=admin.role,
        user_id=user_id,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# Registry custom-fields proxy routes (flexible-document-fields)
# ---------------------------------------------------------------------------


@router.get("/document-types/{type_code}/custom-fields")
async def get_custom_field_schema(
    type_code: str,
    admin: RequireAdmin,
    registry_client: GetRegistryClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Get the custom field schema for a document type (admin-only, no re-MFA for reads)."""
    upstream = await registry_client.get_custom_field_schema(
        actor_id=admin.subject,
        role=admin.role,
        request_id=request_id,
        type_code=type_code,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.put("/document-types/{type_code}/custom-fields")
async def update_custom_field_schema(
    type_code: str,
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    registry_client: GetRegistryClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Replace the custom field schema for a document type (admin + re-MFA)."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    upstream = await registry_client.update_custom_field_schema(
        actor_id=admin.subject,
        role=admin.role,
        request_id=request_id,
        type_code=type_code,
        body=body,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.post("/import/preview")
async def import_xlsx_preview(
    file: UploadFile,
    admin: RequireAdmin,
    registry_client: GetRegistryClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Parse xlsx and classify headers — multipart pass-through to registry-svc (admin-only)."""
    data = await file.read()
    upstream = await registry_client.import_xlsx_preview(
        actor_id=admin.subject,
        role=admin.role,
        request_id=request_id,
        filename=file.filename or "import.xlsx",
        data=data,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


@router.post("/import/confirm")
async def import_xlsx_confirm(
    body: dict[str, Any],
    admin: RequireAdmin,
    auth_client: GetAuthClient,
    registry_client: GetRegistryClient,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Apply import decisions and insert documents (admin + re-MFA)."""
    totp_code = body.pop("totp_code", None)
    await _verify_re_mfa(
        admin=admin,
        totp_code=totp_code,
        auth_client=auth_client,
        request_id=request_id,
    )
    upstream = await registry_client.import_xlsx_confirm(
        actor_id=admin.subject,
        role=admin.role,
        request_id=request_id,
        body=body,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()


# ---------------------------------------------------------------------------
# GET /api/v1/admin/notifications/history  → notification-svc
# Read-only; no re-MFA required.
# ---------------------------------------------------------------------------


@router.get("/notifications/history")
async def list_notifications_history(
    admin: RequireAdmin,
    notification_client: GetNotificationClient,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    template_code: str | None = None,
    channel: str | None = None,
    document_id: str | None = None,
    user_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    request_id: str | None = Depends(get_request_id),
) -> Any:
    """Proxy to notification-svc — paginated delivery_attempts (Phase C)."""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if status:
        params["status"] = status
    if template_code:
        params["template_code"] = template_code
    if channel:
        params["channel"] = channel
    if document_id:
        params["document_id"] = document_id
    if user_id:
        params["user_id"] = user_id
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to

    upstream = await notification_client.admin_list_notifications_history(
        actor_id=admin.subject,
        role=admin.role,
        params=params,
        request_id=request_id,
    )
    if not upstream.is_success:
        raise _upstream_error(upstream)
    return upstream.json()
