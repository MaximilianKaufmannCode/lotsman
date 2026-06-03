# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Admin endpoints — /api/v1/admin/*

All routes require:
  - valid internal JWT (RequireActor)
  - role == "admin" (require_role("admin"))
  - re-MFA gate where ADR-0003 mandates it (require_admin_re_mfa)

Endpoints:
  POST   /admin/users                          — invite user (US-8, US-9, ADR-0004 §5)
  GET    /admin/users                          — list all users
  GET    /admin/users/{user_id}                — get user detail
  PATCH  /admin/users/{user_id}/role           — change role (US-19)
  POST   /admin/users/{user_id}/deactivate     — deactivate user (US-18)
  POST   /admin/users/{user_id}/lockout        — manual lockout
  DELETE /admin/users/{user_id}/lockout        — unlock user
  GET    /admin/users/{user_id}/sessions       — list active sessions (US-21)
  DELETE /admin/users/{user_id}/sessions       — revoke all sessions (US-15)
  DELETE /admin/users/{user_id}/sessions/{sid} — revoke single session
  POST   /admin/users/{user_id}/totp/reset     — reset TOTP (US-16)
  POST   /admin/users/{user_id}/password/reset — reset password (US-20)
  POST   /admin/users/{user_id}/invite         — re-invite pending user (US-10)
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, HTTPException
from lotsman_shared.actors import ACTOR_SYSTEM_MIGRATOR
from lotsman_shared.internal_jwt import InternalJWTClaims
from sqlalchemy.ext.asyncio import AsyncSession

from auth_service.api.deps import (
    AppSettings,
    DbSession,
    get_redis,
    require_admin_re_mfa,
    require_role,
)
from auth_service.api.schemas import (
    AdminUpdateUserProfileRequest,
    ChangeRoleRequest,
    InviteUserAutoResponse,
    InviteUserRequest,
    InviteUserShowOtpResponse,
    PasswordResetResponse,
    ReInviteUserRequest,
    ResetTotpAdminRequest,
    RevokeAllSessionsResponse,
    SessionResponse,
    UserResponse,
)
from auth_service.application.dto import (
    AdminUpdateUserProfileCommand,
    ChangeRoleCommand,
    DeactivateUserCommand,
    DeleteUserCommand,
    InviteUserCommand,
    ListUserSessionsAdminCommand,
    LockoutUserAdminCommand,
    ReactivateUserCommand,
    ReInviteUserCommand,
    ResetPasswordAdminCommand,
    ResetTotpAdminCommand,
    RevokeAllSessionsCommand,
    RevokeSessionCommand,
    UnlockUserAdminCommand,
)
from auth_service.application.use_cases.admin_update_user_profile import AdminUpdateUserProfile
from auth_service.application.use_cases.change_role import ChangeRole
from auth_service.application.use_cases.deactivate_user import DeactivateUser
from auth_service.application.use_cases.delete_user import DeleteUser
from auth_service.application.use_cases.invite_user import InviteUser
from auth_service.application.use_cases.list_user_sessions_admin import ListUserSessionsAdmin
from auth_service.application.use_cases.lockout_user_admin import LockoutUserAdmin
from auth_service.application.use_cases.re_invite_user import ReInviteUser
from auth_service.application.use_cases.reactivate_user import ReactivateUser
from auth_service.application.use_cases.reset_password_admin import ResetPasswordAdmin
from auth_service.application.use_cases.reset_totp_admin import ResetTotpAdmin
from auth_service.application.use_cases.revoke_all_sessions import RevokeAllSessions
from auth_service.application.use_cases.revoke_session import RevokeSession
from auth_service.application.use_cases.unlock_user_admin import UnlockUserAdmin
from auth_service.infrastructure.channel_directory_http import ChannelDirectoryHttpAdapter
from auth_service.infrastructure.db.repositories import (
    SqlaBackupCodeRepository,
    SqlaEventOutbox,
    SqlaSessionRepository,
    SqlaTotpUsedCodeRepository,
    SqlaUserRepository,
)
from auth_service.infrastructure.password import Argon2PasswordHasher
from auth_service.infrastructure.redis.invite_otp_publisher import RedisInviteOtpPublisher
from auth_service.infrastructure.redis.lockout_store import RedisLockoutStore
from auth_service.infrastructure.totp import PyotpTotpService
from auth_service.infrastructure.totp_crypto import FernetEncryptionService

log = structlog.get_logger(__name__)

# Typed aliases for cleaner signatures
AdminActor = Annotated[InternalJWTClaims, Depends(require_role("admin"))]
AdminActorReMfa = Annotated[InternalJWTClaims, Depends(require_admin_re_mfa)]

# Router-level admin role gate on all routes (belt-and-suspenders; each
# endpoint also declares its own dependency for the return type).
router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_role("admin"))],
)


def _build_admin_context(
    session: AsyncSession,
    settings: AppSettings,
    redis: aioredis.Redis,
) -> dict[str, Any]:
    """Wire common infrastructure adapters for admin use cases."""
    return {
        "user_repo": SqlaUserRepository(session),
        "session_repo": SqlaSessionRepository(session),
        "backup_code_repo": SqlaBackupCodeRepository(session),
        "totp_used_repo": SqlaTotpUsedCodeRepository(session),
        "outbox": SqlaEventOutbox(session),
        "hasher": Argon2PasswordHasher(),
        "totp_service": PyotpTotpService(),
        "encryption_service": FernetEncryptionService(settings.totp_enc_key),
        "lockout_store": RedisLockoutStore(redis),
    }


# ---------------------------------------------------------------------------
# POST /admin/users — invite user (US-8, US-9, ADR-0004 §5)
# Re-MFA gate: YES (ADR-0003 — user provisioning is a sensitive admin action)
#
# Replaces the old CreateUser endpoint. Accepts delivery="auto"|"show-otp".
# Response shape differs by delivery mode:
#   auto     → {user_id, channel_used, invitation_id}
#   show-otp → {user_id, otp, otp_ttl_minutes}
# ---------------------------------------------------------------------------


@router.post("/users", status_code=201)
async def invite_user(
    body: InviteUserRequest,
    db: DbSession,
    settings: AppSettings,
    actor: AdminActorReMfa,
    redis=Depends(get_redis),
) -> InviteUserAutoResponse | InviteUserShowOtpResponse:
    ctx = _build_admin_context(db, settings, redis)

    # Build the HTTP-adapter for channel lookup (calls notification-svc).
    channel_reader = ChannelDirectoryHttpAdapter(
        notification_svc_url=settings.notification_svc_url,
        signing_key=settings.internal_jwt_key_notification,
        system_actor_id=ACTOR_SYSTEM_MIGRATOR,
    )

    from auth_service.application.dto import InviteUserAutoDTO
    from auth_service.domain.errors import NoEnabledChannelError

    async with db.begin():
        use_case = InviteUser(
            user_repo=ctx["user_repo"],
            hasher=ctx["hasher"],
            outbox=ctx["outbox"],
            channel_reader=channel_reader,
            otp_publisher=RedisInviteOtpPublisher(redis),
        )
        try:
            result = await use_case.execute(
                cmd=InviteUserCommand(
                    email=body.email,
                    full_name=body.full_name,
                    role=body.role,
                    delivery=body.delivery,
                    actor_id=actor.actor_id,
                )
            )
        except NoEnabledChannelError as exc:
            raise HTTPException(status_code=409, detail=exc.message) from exc

    log.info(
        "admin.user_invited",
        actor_id=str(actor.actor_id),
        delivery=body.delivery,
    )

    if isinstance(result, InviteUserAutoDTO):
        return InviteUserAutoResponse(
            user_id=result.user_id,
            channel_used=result.channel_used,
            invitation_id=result.invitation_id,
        )
    else:
        return InviteUserShowOtpResponse(
            user_id=result.user_id,
            otp=result.otp,
            otp_ttl_minutes=result.otp_ttl_minutes,
        )


# ---------------------------------------------------------------------------
# GET /admin/users — list all users
# Re-MFA gate: NO (read-only)
# ---------------------------------------------------------------------------


@router.get("/users")
async def list_users(
    db: DbSession,
    settings: AppSettings,
    actor: AdminActor,
    redis=Depends(get_redis),
) -> list[UserResponse]:
    ctx = _build_admin_context(db, settings, redis)
    users = await ctx["user_repo"].list_all()
    lockout_store = ctx["lockout_store"]
    result = []
    for u in users:
        is_locked = await lockout_store.is_locked(u.id)
        result.append(
            UserResponse(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                role=u.role,
                is_active=u.is_active,
                must_change_password=u.must_change_password,
                last_login_at=u.last_login_at,
                created_at=u.created_at,
                updated_at=u.updated_at,
                totp_enrolled=u.has_totp_enrolled,
                is_locked=is_locked,
            )
        )
    return result


# ---------------------------------------------------------------------------
# GET /admin/users/{user_id} — get single user
# Re-MFA gate: NO (read-only)
# ---------------------------------------------------------------------------


@router.get("/users/{user_id}")
async def get_user(
    user_id: uuid.UUID,
    db: DbSession,
    settings: AppSettings,
    actor: AdminActor,
    redis=Depends(get_redis),
) -> UserResponse:
    ctx = _build_admin_context(db, settings, redis)
    user = await ctx["user_repo"].get_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    is_locked = await ctx["lockout_store"].is_locked(user.id)
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
        totp_enrolled=user.has_totp_enrolled,
        is_locked=is_locked,
    )


# ---------------------------------------------------------------------------
# PATCH /admin/users/{user_id}/role — change role (US-19)
# Re-MFA gate: YES (role change is privilege escalation)
# ---------------------------------------------------------------------------


@router.patch("/users/{user_id}/role")
async def change_role(
    user_id: uuid.UUID,
    body: ChangeRoleRequest,
    db: DbSession,
    settings: AppSettings,
    actor: AdminActorReMfa,
    redis=Depends(get_redis),
) -> UserResponse:
    ctx = _build_admin_context(db, settings, redis)

    async with db.begin():
        use_case = ChangeRole(
            user_repo=ctx["user_repo"],
            outbox=ctx["outbox"],
        )
        result = await use_case.execute(
            cmd=ChangeRoleCommand(
                target_user_id=user_id,
                new_role=body.role,
                actor_id=actor.actor_id,
            )
        )

    log.info(
        "admin.role_changed",
        actor_id=str(actor.actor_id),
        target_user_id=str(user_id),
        new_role=body.role,
    )
    # Fetch totp_enrolled from the entity (UserDTO does not carry it).
    user_entity = await ctx["user_repo"].get_by_id(user_id)
    is_locked = await ctx["lockout_store"].is_locked(user_id)
    return UserResponse(
        id=result.id,
        email=result.email,
        full_name=result.full_name,
        role=result.role,
        is_active=result.is_active,
        must_change_password=result.must_change_password,
        last_login_at=result.last_login_at,
        created_at=result.created_at,
        updated_at=result.updated_at,
        totp_enrolled=user_entity.has_totp_enrolled if user_entity else False,
        is_locked=is_locked,
    )


# ---------------------------------------------------------------------------
# PATCH /admin/users/{user_id}/profile — admin updates target user's profile (US-103)
# Currently only full_name. Email change deferred to a separate ADR.
# Re-MFA gate: YES (mutation of another user's data).
# ---------------------------------------------------------------------------


@router.patch("/users/{user_id}/profile")
async def admin_update_user_profile(
    user_id: uuid.UUID,
    body: AdminUpdateUserProfileRequest,
    db: DbSession,
    settings: AppSettings,
    actor: AdminActorReMfa,
    redis=Depends(get_redis),
) -> UserResponse:
    ctx = _build_admin_context(db, settings, redis)

    async with db.begin():
        use_case = AdminUpdateUserProfile(
            user_repo=ctx["user_repo"],
            outbox=ctx["outbox"],
        )
        result = await use_case.execute(
            cmd=AdminUpdateUserProfileCommand(
                target_user_id=user_id,
                actor_id=actor.actor_id,
                full_name=body.full_name,
            )
        )

    log.info(
        "admin.user_profile_updated",
        actor_id=str(actor.actor_id),
        target_user_id=str(user_id),
    )
    user_entity = await ctx["user_repo"].get_by_id(user_id)
    is_locked = await ctx["lockout_store"].is_locked(user_id)
    return UserResponse(
        id=result.id,
        email=result.email,
        full_name=result.full_name,
        role=result.role,
        is_active=result.is_active,
        must_change_password=result.must_change_password,
        last_login_at=result.last_login_at,
        created_at=result.created_at,
        updated_at=result.updated_at,
        totp_enrolled=user_entity.has_totp_enrolled if user_entity else False,
        is_locked=is_locked,
    )


# ---------------------------------------------------------------------------
# POST /admin/users/{user_id}/deactivate — deactivate user (US-18)
# Re-MFA gate: YES (destructive operation)
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/deactivate", status_code=204)
async def deactivate_user(
    user_id: uuid.UUID,
    db: DbSession,
    settings: AppSettings,
    actor: AdminActorReMfa,
    redis=Depends(get_redis),
) -> None:
    ctx = _build_admin_context(db, settings, redis)

    async with db.begin():
        use_case = DeactivateUser(
            user_repo=ctx["user_repo"],
            session_repo=ctx["session_repo"],
            lockout_store=ctx["lockout_store"],
            outbox=ctx["outbox"],
        )
        await use_case.execute(
            cmd=DeactivateUserCommand(
                target_user_id=user_id,
                actor_id=actor.actor_id,
            )
        )

    log.info("admin.user_deactivated", actor_id=str(actor.actor_id), target_user_id=str(user_id))


# ---------------------------------------------------------------------------
# POST /admin/users/{user_id}/reactivate — restore a soft-deleted user (US-104)
# Re-MFA gate: YES (destructive-ish — re-enables login)
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/reactivate", status_code=204)
async def reactivate_user(
    user_id: uuid.UUID,
    db: DbSession,
    settings: AppSettings,
    actor: AdminActorReMfa,
    redis=Depends(get_redis),
) -> None:
    ctx = _build_admin_context(db, settings, redis)

    async with db.begin():
        use_case = ReactivateUser(
            user_repo=ctx["user_repo"],
            lockout_store=ctx["lockout_store"],
            outbox=ctx["outbox"],
        )
        await use_case.execute(
            cmd=ReactivateUserCommand(
                target_user_id=user_id,
                actor_id=actor.actor_id,
            )
        )

    log.info("admin.user_reactivated", actor_id=str(actor.actor_id), target_user_id=str(user_id))


# ---------------------------------------------------------------------------
# DELETE /admin/users/{user_id} — permanent soft-delete (hide + free email)
# Re-MFA gate: YES (destructive, irreversible from UI)
# ---------------------------------------------------------------------------


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    db: DbSession,
    settings: AppSettings,
    actor: AdminActorReMfa,
    redis=Depends(get_redis),
) -> None:
    ctx = _build_admin_context(db, settings, redis)

    async with db.begin():
        use_case = DeleteUser(
            user_repo=ctx["user_repo"],
            session_repo=ctx["session_repo"],
            lockout_store=ctx["lockout_store"],
            outbox=ctx["outbox"],
        )
        await use_case.execute(
            cmd=DeleteUserCommand(
                target_user_id=user_id,
                actor_id=actor.actor_id,
            )
        )

    log.info("admin.user_deleted", actor_id=str(actor.actor_id), target_user_id=str(user_id))


# ---------------------------------------------------------------------------
# POST /admin/users/{user_id}/lockout — manual lockout
# Re-MFA gate: YES (security operation)
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/lockout", status_code=204)
async def lockout_user(
    user_id: uuid.UUID,
    db: DbSession,
    settings: AppSettings,
    actor: AdminActorReMfa,
    redis=Depends(get_redis),
) -> None:
    ctx = _build_admin_context(db, settings, redis)

    async with db.begin():
        use_case = LockoutUserAdmin(
            user_repo=ctx["user_repo"],
            session_repo=ctx["session_repo"],
            lockout_store=ctx["lockout_store"],
            outbox=ctx["outbox"],
        )
        await use_case.execute(
            cmd=LockoutUserAdminCommand(
                actor_id=actor.actor_id,
                target_user_id=user_id,
            )
        )

    log.info("admin.user_locked", actor_id=str(actor.actor_id), target_user_id=str(user_id))


# ---------------------------------------------------------------------------
# DELETE /admin/users/{user_id}/lockout — unlock user
# Re-MFA gate: YES (security operation)
# ---------------------------------------------------------------------------


@router.delete("/users/{user_id}/lockout", status_code=204)
async def unlock_user(
    user_id: uuid.UUID,
    db: DbSession,
    settings: AppSettings,
    actor: AdminActorReMfa,
    redis=Depends(get_redis),
) -> None:
    ctx = _build_admin_context(db, settings, redis)

    async with db.begin():
        use_case = UnlockUserAdmin(
            user_repo=ctx["user_repo"],
            lockout_store=ctx["lockout_store"],
            outbox=ctx["outbox"],
        )
        await use_case.execute(
            cmd=UnlockUserAdminCommand(
                actor_id=actor.actor_id,
                target_user_id=user_id,
            )
        )

    log.info("admin.user_unlocked", actor_id=str(actor.actor_id), target_user_id=str(user_id))


# ---------------------------------------------------------------------------
# GET /admin/users/{user_id}/sessions — list active sessions (US-21)
# Re-MFA gate: NO (read-only)
# ---------------------------------------------------------------------------


@router.get("/users/{user_id}/sessions")
async def list_user_sessions(
    user_id: uuid.UUID,
    db: DbSession,
    settings: AppSettings,
    actor: AdminActor,
    redis=Depends(get_redis),
) -> list[SessionResponse]:
    ctx = _build_admin_context(db, settings, redis)

    use_case = ListUserSessionsAdmin(
        user_repo=ctx["user_repo"],
        session_repo=ctx["session_repo"],
    )
    sessions = await use_case.execute(
        cmd=ListUserSessionsAdminCommand(
            actor_id=actor.actor_id,
            target_user_id=user_id,
        )
    )
    return [
        SessionResponse(
            id=s.id,
            user_id=s.user_id,
            user_agent=s.user_agent,
            ip_address=s.ip_address,
            created_at=s.created_at,
            expires_at=s.expires_at,
            is_current=s.is_current,
        )
        for s in sessions
    ]


# ---------------------------------------------------------------------------
# DELETE /admin/users/{user_id}/sessions — revoke ALL sessions (US-15)
# Re-MFA gate: YES (forceful logout of another user)
# ---------------------------------------------------------------------------


@router.delete("/users/{user_id}/sessions")
async def revoke_all_user_sessions(
    user_id: uuid.UUID,
    db: DbSession,
    settings: AppSettings,
    actor: AdminActorReMfa,
    redis=Depends(get_redis),
) -> RevokeAllSessionsResponse:
    ctx = _build_admin_context(db, settings, redis)

    async with db.begin():
        use_case = RevokeAllSessions(
            user_repo=ctx["user_repo"],
            session_repo=ctx["session_repo"],
            outbox=ctx["outbox"],
        )
        result = await use_case.execute(
            cmd=RevokeAllSessionsCommand(
                actor_id=actor.actor_id,
                target_user_id=user_id,
            )
        )

    log.info(
        "admin.sessions_revoked_all",
        actor_id=str(actor.actor_id),
        target_user_id=str(user_id),
        revoked_count=result.revoked_count,
    )
    return RevokeAllSessionsResponse(revoked_count=result.revoked_count)


# ---------------------------------------------------------------------------
# DELETE /admin/users/{user_id}/sessions/{session_id} — revoke single session
# Re-MFA gate: YES (forceful logout of another user)
# ---------------------------------------------------------------------------


@router.delete("/users/{user_id}/sessions/{session_id}", status_code=204)
async def revoke_user_session(
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    db: DbSession,
    settings: AppSettings,
    actor: AdminActorReMfa,
    redis=Depends(get_redis),
) -> None:
    ctx = _build_admin_context(db, settings, redis)

    async with db.begin():
        use_case = RevokeSession(
            session_repo=ctx["session_repo"],
            outbox=ctx["outbox"],
        )
        await use_case.execute(
            cmd=RevokeSessionCommand(
                actor_id=actor.actor_id,
                actor_role="admin",
                target_user_id=user_id,
                session_id=session_id,
            )
        )

    log.info(
        "admin.session_revoked",
        actor_id=str(actor.actor_id),
        target_user_id=str(user_id),
        session_id=str(session_id),
    )


# ---------------------------------------------------------------------------
# POST /admin/users/{user_id}/totp/reset — admin resets target TOTP (US-16)
# Re-MFA gate: INLINE (use case verifies admin TOTP code directly, not Redis flag)
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/totp/reset", status_code=204)
async def reset_user_totp(
    user_id: uuid.UUID,
    body: ResetTotpAdminRequest,
    db: DbSession,
    settings: AppSettings,
    actor: AdminActor,
    redis=Depends(get_redis),
) -> None:
    """Reset TOTP for a target user.

    The use case performs its own re-MFA gate inline (verifies admin_totp_code
    against the admin's own TOTP). Does NOT use the Redis mfa-verified flag
    because the admin must prove intent at time of request.
    """
    ctx = _build_admin_context(db, settings, redis)

    async with db.begin():
        use_case = ResetTotpAdmin(
            user_repo=ctx["user_repo"],
            session_repo=ctx["session_repo"],
            backup_code_repo=ctx["backup_code_repo"],
            totp_service=ctx["totp_service"],
            encryption_service=ctx["encryption_service"],
            outbox=ctx["outbox"],
        )
        await use_case.execute(
            cmd=ResetTotpAdminCommand(
                actor_id=actor.actor_id,
                target_user_id=user_id,
                admin_totp_code=body.admin_totp_code,
            )
        )

    log.info(
        "admin.totp_reset",
        actor_id=str(actor.actor_id),
        target_user_id=str(user_id),
    )


# ---------------------------------------------------------------------------
# POST /admin/users/{user_id}/password/reset — admin resets password (US-20)
# Re-MFA gate: YES (generates new credentials)
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/password/reset")
async def reset_user_password(
    user_id: uuid.UUID,
    db: DbSession,
    settings: AppSettings,
    actor: AdminActorReMfa,
    redis=Depends(get_redis),
) -> PasswordResetResponse:
    ctx = _build_admin_context(db, settings, redis)

    async with db.begin():
        use_case = ResetPasswordAdmin(
            user_repo=ctx["user_repo"],
            session_repo=ctx["session_repo"],
            hasher=ctx["hasher"],
            outbox=ctx["outbox"],
        )
        result = await use_case.execute(
            cmd=ResetPasswordAdminCommand(
                actor_id=actor.actor_id,
                target_user_id=user_id,
            )
        )

    log.info(
        "admin.password_reset",
        actor_id=str(actor.actor_id),
        target_user_id=str(user_id),
    )
    return PasswordResetResponse(oob_otp=result.oob_otp)


# ---------------------------------------------------------------------------
# POST /admin/users/{user_id}/invite — re-invite pending user (US-10)
# Re-MFA gate: YES (generates new credentials, sends notification)
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/invite", status_code=200)
async def re_invite_user(
    user_id: uuid.UUID,
    body: ReInviteUserRequest,
    db: DbSession,
    settings: AppSettings,
    actor: AdminActorReMfa,
    redis=Depends(get_redis),
) -> InviteUserAutoResponse | InviteUserShowOtpResponse:
    """Re-invite a pending user (invalidates old OTP, issues new one). US-10."""
    ctx = _build_admin_context(db, settings, redis)

    channel_reader = ChannelDirectoryHttpAdapter(
        notification_svc_url=settings.notification_svc_url,
        signing_key=settings.internal_jwt_key_notification,
        system_actor_id=ACTOR_SYSTEM_MIGRATOR,
    )

    from auth_service.application.dto import InviteUserAutoDTO
    from auth_service.domain.errors import (
        NoEnabledChannelError,
        UserAlreadyActivatedError,
    )

    async with db.begin():
        use_case = ReInviteUser(
            user_repo=ctx["user_repo"],
            hasher=ctx["hasher"],
            outbox=ctx["outbox"],
            channel_reader=channel_reader,
            otp_publisher=RedisInviteOtpPublisher(redis),
        )
        try:
            result = await use_case.execute(
                cmd=ReInviteUserCommand(
                    target_user_id=user_id,
                    actor_id=actor.actor_id,
                    delivery=body.delivery,
                )
            )
        except NoEnabledChannelError as exc:
            raise HTTPException(status_code=409, detail=exc.message) from exc
        except UserAlreadyActivatedError as exc:
            raise HTTPException(status_code=409, detail=exc.message) from exc

    log.info(
        "admin.user_re_invited",
        actor_id=str(actor.actor_id),
        target_user_id=str(user_id),
    )

    if isinstance(result, InviteUserAutoDTO):
        return InviteUserAutoResponse(
            user_id=result.user_id,
            channel_used=result.channel_used,
            invitation_id=result.invitation_id,
        )
    else:
        return InviteUserShowOtpResponse(
            user_id=result.user_id,
            otp=result.otp,
            otp_ttl_minutes=result.otp_ttl_minutes,
        )
