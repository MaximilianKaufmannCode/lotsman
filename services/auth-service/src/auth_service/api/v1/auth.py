# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Auth flow endpoints — /api/v1/auth/*

Endpoints:
  POST /login
  POST /totp/verify
  POST /totp/enroll
  POST /totp/enroll/confirm
  POST /backup-codes/regenerate
  POST /mfa-check
  POST /refresh
  POST /logout
  GET  /sessions
  DELETE /sessions/{session_id}
  POST /change-password
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from auth_service.api.deps import (
    AppSettings,
    DbSession,
    RequireActor,
    current_actor,
    get_redis,
    require_actor,
)
from auth_service.api.schemas import (
    BackupCodesRegeneratedResponse,
    ConfirmTotpEnrollmentEnrollmentRequest,
    EnrollTotpEnrollmentRequest,
    LoginPendingEnrollResponse,
    LoginPendingTotpResponse,
    LoginRequest,
    LoginSuccessResponse,
    ReMfaCheckRequest,
    ReMfaCheckResponse,
    SessionResponse,
    TotpConfirmResponse,
    TotpEnrollResponse,
    VerifyTotpRequest,
)
from auth_service.application.dto import (
    ChangePasswordCommand,
    ConfirmTotpEnrollmentCommand,
    ConfirmTotpEnrollmentTerminalDTO,
    EnrollTotpCommand,
    ListMySessionsCommand,
    LogoutCommand,
    RefreshTokensCommand,
    RegenerateBackupCodesCommand,
    ReMfaCheckCommand,
    RevokeSessionCommand,
    StartLoginCommand,
    VerifyTotpCommand,
)
from auth_service.application.use_cases.change_password import ChangePassword
from auth_service.application.use_cases.confirm_totp_enrollment import ConfirmTotpEnrollment
from auth_service.application.use_cases.enroll_totp import EnrollTotp
from auth_service.application.use_cases.list_my_sessions import ListMySessions
from auth_service.application.use_cases.logout import Logout
from auth_service.application.use_cases.re_mfa_check import ReMfaCheck
from auth_service.application.use_cases.refresh_tokens import RefreshTokens
from auth_service.application.use_cases.regenerate_backup_codes import RegenerateBackupCodes
from auth_service.application.use_cases.resolve_enrollment_ticket import ResolveEnrollmentTicket
from auth_service.application.use_cases.revoke_session import RevokeSession
from auth_service.application.use_cases.start_login import StartLogin
from auth_service.application.use_cases.verify_totp import VerifyTotp
from auth_service.domain.errors import (
    InvalidCredentialsError,
    SessionExpiredError,
)
from auth_service.infrastructure.breached_passwords import LocalHibpChecker
from auth_service.infrastructure.db.repositories import (
    SqlaBackupCodeRepository,
    SqlaEventOutbox,
    SqlaLoginAttemptRepository,
    SqlaSessionRepository,
    SqlaTotpUsedCodeRepository,
    SqlaUserRepository,
)
from auth_service.infrastructure.jwt_issuer import RS256JwtIssuer
from auth_service.infrastructure.password import Argon2PasswordHasher
from auth_service.infrastructure.redis.enrollment_store import RedisTotpEnrollmentStore
from auth_service.infrastructure.redis.pending_totp_store import RedisPendingTotpLoginStore
from auth_service.infrastructure.redis.re_mfa_store import RedisReMfaStore
from auth_service.infrastructure.totp import PyotpTotpService
from auth_service.infrastructure.totp_crypto import FernetEncryptionService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_COOKIE_NAME = "refresh"


def _clear_refresh_cookie(response: Response, settings: AppSettings) -> None:
    response.delete_cookie(
        key=_REFRESH_COOKIE_NAME,
        path="/api/v1/auth",
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
    )


def _set_refresh_cookie(response: Response, token: str, settings: AppSettings) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        path="/api/v1/auth",
        max_age=settings.refresh_token_ttl_seconds,
    )


def _build_use_case_context(
    session: AsyncSession,
    settings: AppSettings,
    redis: aioredis.Redis,
) -> dict[str, Any]:
    """Build the common infrastructure adapters for use cases."""

    user_repo = SqlaUserRepository(session)
    session_repo = SqlaSessionRepository(session)
    attempts_repo = SqlaLoginAttemptRepository(session)
    backup_code_repo = SqlaBackupCodeRepository(session)
    totp_used_repo = SqlaTotpUsedCodeRepository(session)
    outbox = SqlaEventOutbox(session)
    hasher = Argon2PasswordHasher()
    totp_service = PyotpTotpService()
    encryption_service = FernetEncryptionService(settings.totp_enc_key)
    pending_totp_store = RedisPendingTotpLoginStore(redis)
    enrollment_store = RedisTotpEnrollmentStore(redis)
    re_mfa_store = RedisReMfaStore(redis)
    oob_otp_store = None  # not needed in most auth routes
    hibp_checker = LocalHibpChecker()

    try:
        jwt_issuer = RS256JwtIssuer(
            settings.jwt_private_key_path,
            kid=settings.jwt_current_kid,
            ttl_seconds=settings.access_token_ttl_seconds,
        )
    except Exception:
        jwt_issuer = None  # may fail if key file absent in tests

    return {
        "user_repo": user_repo,
        "session_repo": session_repo,
        "attempts_repo": attempts_repo,
        "backup_code_repo": backup_code_repo,
        "totp_used_repo": totp_used_repo,
        "outbox": outbox,
        "hasher": hasher,
        "totp_service": totp_service,
        "encryption_service": encryption_service,
        "pending_totp_store": pending_totp_store,
        "enrollment_store": enrollment_store,
        "re_mfa_store": re_mfa_store,
        "hibp_checker": hibp_checker,
        "jwt_issuer": jwt_issuer,
        "oob_otp_store": oob_otp_store,
    }


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    redis=Depends(get_redis),
) -> LoginPendingTotpResponse | LoginPendingEnrollResponse | LoginSuccessResponse:
    ctx = _build_use_case_context(db, settings, redis)

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    async with db.begin():
        use_case = StartLogin(
            user_repo=ctx["user_repo"],
            attempts_repo=ctx["attempts_repo"],
            hasher=ctx["hasher"],
            oob_otp_store=ctx["oob_otp_store"],
            pending_totp_store=ctx["pending_totp_store"],
            outbox=ctx["outbox"],
        )
        result = await use_case.execute(
            cmd=StartLoginCommand(
                email=body.email,
                password=body.password,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        )

    from auth_service.application.dto import LoginPendingEnrollDTO, LoginPendingTotpDTO

    if isinstance(result, LoginPendingTotpDTO):
        return LoginPendingTotpResponse(session_ticket=result.session_ticket)
    if isinstance(result, LoginPendingEnrollDTO):
        return LoginPendingEnrollResponse(enrollment_token=result.enrollment_token)
    raise HTTPException(status_code=500, detail="Unexpected login result")


# ---------------------------------------------------------------------------
# POST /auth/totp/verify
# ---------------------------------------------------------------------------


@router.post("/totp/verify")
async def verify_totp_endpoint(
    body: VerifyTotpRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    redis=Depends(get_redis),
) -> LoginSuccessResponse:
    ctx = _build_use_case_context(db, settings, redis)
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    async with db.begin():
        use_case = VerifyTotp(
            user_repo=ctx["user_repo"],
            session_repo=ctx["session_repo"],
            attempts_repo=ctx["attempts_repo"],
            totp_service=ctx["totp_service"],
            encryption_service=ctx["encryption_service"],
            backup_code_repo=ctx["backup_code_repo"],
            totp_used_repo=ctx["totp_used_repo"],
            pending_totp_store=ctx["pending_totp_store"],
            jwt_issuer=ctx["jwt_issuer"],
            hasher=ctx["hasher"],
            outbox=ctx["outbox"],
            session_ttl_seconds=settings.refresh_token_ttl_seconds,
        )
        result = await use_case.execute(
            cmd=VerifyTotpCommand(
                ticket_id=body.session_ticket,
                totp_code=body.totp_code,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        )

    # Internal API: return refresh in body. web-bff sets the HttpOnly cookie
    # with browser-appropriate attributes (env-driven Secure/SameSite).
    return LoginSuccessResponse(
        access_token=result.access_token,
        backup_codes_warning=result.backup_codes_warning,
        refresh_token=result.refresh_token,
    )


# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------


@router.post("/refresh")
async def refresh_tokens_endpoint(
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    redis=Depends(get_redis),
) -> LoginSuccessResponse:
    refresh_token = request.cookies.get(_REFRESH_COOKIE_NAME)
    if not refresh_token:
        _clear_refresh_cookie(response, settings)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    ctx = _build_use_case_context(db, settings, redis)
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    try:
        async with db.begin():
            use_case = RefreshTokens(
                user_repo=ctx["user_repo"],
                session_repo=ctx["session_repo"],
                jwt_issuer=ctx["jwt_issuer"],
                outbox=ctx["outbox"],
            )
            result = await use_case.execute(
                cmd=RefreshTokensCommand(
                    refresh_token=refresh_token,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            )
    except (InvalidCredentialsError, SessionExpiredError):
        _clear_refresh_cookie(response, settings)
        raise

    return LoginSuccessResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
    )


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------


@router.post("/logout", status_code=204)
async def logout_endpoint(
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    actor: RequireActor,
    redis: Annotated[Any, Depends(get_redis)] = None,
) -> None:
    refresh_token = request.cookies.get(_REFRESH_COOKIE_NAME)
    ctx = _build_use_case_context(db, settings, redis)

    async with db.begin():
        use_case = Logout(
            session_repo=ctx["session_repo"],
            outbox=ctx["outbox"],
        )
        await use_case.execute(
            cmd=LogoutCommand(refresh_token=refresh_token),
            actor_id=actor.actor_id,
        )

    _clear_refresh_cookie(response, settings)


# ---------------------------------------------------------------------------
# Enrollment ticket resolver (ADR-0008 D3b / MF-5 / MF-4)
# ---------------------------------------------------------------------------


def _make_enrollment_ticket_resolver(ctx: dict[str, Any]) -> ResolveEnrollmentTicket:
    """Build a ResolveEnrollmentTicket use case from the pre-built context dict."""
    return ResolveEnrollmentTicket(
        pending_totp_store=ctx["pending_totp_store"],
        user_repo=ctx["user_repo"],
    )


# ---------------------------------------------------------------------------
# POST /auth/totp/enroll  (enrollment-ticket lane — ADR-0008 D3 / MF-5)
# ---------------------------------------------------------------------------


@router.post("/totp/enroll")
async def enroll_totp_endpoint(
    body: EnrollTotpEnrollmentRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    redis: Annotated[Any, Depends(get_redis)] = None,
) -> TotpEnrollResponse:
    """Step 1 of TOTP enrollment — anonymous ticket lane (ADR-0008 D2/D3/MF-5).

    MF-3/D3a.2: Request body MUST NOT be logged.
    No RequireActor dependency — identity comes from ticket only.
    """
    ctx = _build_use_case_context(db, settings, redis)
    # MF-5: resolve user from ticket exclusively (no actor JWT, no body hint).
    user_id = await _make_enrollment_ticket_resolver(ctx).execute(ticket_id=body.enrollment_token)

    async def get_email(uid: uuid.UUID) -> str:
        user = await ctx["user_repo"].get_by_id(uid)
        return user.email if user else ""

    use_case = EnrollTotp(
        totp_service=ctx["totp_service"],
        enrollment_store=ctx["enrollment_store"],
        user_email_getter=get_email,
    )
    result = await use_case.execute(cmd=EnrollTotpCommand(user_id=user_id))
    return TotpEnrollResponse(secret_b32=result.secret_b32, otpauth_url=result.otpauth_url)


# ---------------------------------------------------------------------------
# POST /auth/totp/enroll/confirm  (enrollment-ticket lane — ADR-0008 D3 / MF-5/6)
# ---------------------------------------------------------------------------


@router.post("/totp/enroll/confirm")
async def confirm_totp_enrollment_endpoint(
    body: ConfirmTotpEnrollmentEnrollmentRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    redis: Annotated[Any, Depends(get_redis)] = None,
) -> TotpConfirmResponse | LoginSuccessResponse:
    """Step 2 of enrollment — anonymous ticket lane (ADR-0008 D3 / MF-5/6).

    Terminal branch (must_change_password=False): returns backup_codes + tokens.
    Non-terminal branch (must_change_password=True): returns backup_codes only.

    MF-3/D3a.2: Request body MUST NOT be logged.
    MF-6: Per-ticket confirm-attempt cap enforced in the use case.
    No RequireActor dependency.
    """
    ctx = _build_use_case_context(db, settings, redis)
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    # MF-5: resolve user from ticket exclusively.
    # Note: the MF-4 enrolled-check happens in ResolveEnrollmentTicket but
    # we pass enrollment_token to the use case as well for cap tracking (MF-6).
    # Resolver MUST run INSIDE db.begin(): user_repo.get_by_id() inside the
    # resolver implicitly opens an autobegin transaction on the SQLAlchemy
    # AsyncSession; a subsequent explicit `async with db.begin()` then raises
    # InvalidRequestError "A transaction is already begun on this Session".
    async with db.begin():
        user_id = await _make_enrollment_ticket_resolver(ctx).execute(
            ticket_id=body.enrollment_token
        )
        use_case = ConfirmTotpEnrollment(
            user_repo=ctx["user_repo"],
            totp_service=ctx["totp_service"],
            encryption_service=ctx["encryption_service"],
            enrollment_store=ctx["enrollment_store"],
            backup_code_repo=ctx["backup_code_repo"],
            hasher=ctx["hasher"],
            outbox=ctx["outbox"],
            # Terminal-branch dependencies (IssueSession collaborator):
            pending_totp_store=ctx["pending_totp_store"],
            session_repo=ctx["session_repo"],
            jwt_issuer=ctx["jwt_issuer"],
            attempts_repo=ctx["attempts_repo"],
            session_ttl_seconds=settings.refresh_token_ttl_seconds,
        )
        result = await use_case.execute(
            cmd=ConfirmTotpEnrollmentCommand(user_id=user_id, code=body.code),
            enrollment_token=body.enrollment_token,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    # Discriminate on result type (ADR-0008 D5.4.9):
    if isinstance(result, ConfirmTotpEnrollmentTerminalDTO):
        # Terminal branch — set refresh cookie, return backup_codes + tokens.
        _set_refresh_cookie(response, result.refresh_token, settings)
        return TotpConfirmResponse(
            backup_codes=result.backup_codes,
            access_token=result.access_token,
            refresh_token=result.refresh_token,
        )
    # Non-terminal branch — return backup codes only (ticket still alive).
    return TotpConfirmResponse(
        backup_codes=result.backup_codes,
        access_token=None,
        refresh_token=None,
    )


# ---------------------------------------------------------------------------
# POST /auth/backup-codes/regenerate
# ---------------------------------------------------------------------------


@router.post("/backup-codes/regenerate")
async def regenerate_backup_codes_endpoint(
    db: DbSession,
    settings: AppSettings,
    actor: RequireActor,
    redis: Annotated[Any, Depends(get_redis)] = None,
) -> BackupCodesRegeneratedResponse:
    ctx = _build_use_case_context(db, settings, redis)
    # session_id from actor — the internal JWT doesn't carry it; use actor_id as a proxy
    session_id = uuid.UUID(int=0)  # placeholder until sid is in internal JWT

    async with db.begin():
        use_case = RegenerateBackupCodes(
            backup_code_repo=ctx["backup_code_repo"],
            hasher=ctx["hasher"],
            re_mfa_store=ctx["re_mfa_store"],
            outbox=ctx["outbox"],
        )
        result = await use_case.execute(
            cmd=RegenerateBackupCodesCommand(user_id=actor.actor_id, session_id=session_id)
        )

    return BackupCodesRegeneratedResponse(backup_codes=result.backup_codes)


# ---------------------------------------------------------------------------
# POST /auth/mfa-check
# ---------------------------------------------------------------------------


@router.post("/mfa-check")
async def re_mfa_check_endpoint(
    body: ReMfaCheckRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    actor: RequireActor,
    redis: Annotated[Any, Depends(get_redis)] = None,
) -> ReMfaCheckResponse:
    ctx = _build_use_case_context(db, settings, redis)
    ip_address = request.client.host if request.client else None
    session_id = uuid.UUID(int=0)  # placeholder

    async with db.begin():
        use_case = ReMfaCheck(
            user_repo=ctx["user_repo"],
            totp_service=ctx["totp_service"],
            encryption_service=ctx["encryption_service"],
            totp_used_repo=ctx["totp_used_repo"],
            re_mfa_store=ctx["re_mfa_store"],
            attempts_repo=ctx["attempts_repo"],
            outbox=ctx["outbox"],
        )
        result = await use_case.execute(
            cmd=ReMfaCheckCommand(
                user_id=actor.actor_id,
                session_id=session_id,
                totp_code=body.totp_code,
                ip_address=ip_address,
            )
        )

    return ReMfaCheckResponse(mfa_verified=result.mfa_verified)


# ---------------------------------------------------------------------------
# GET /auth/sessions
# ---------------------------------------------------------------------------


@router.get("/sessions")
async def list_my_sessions_endpoint(
    db: DbSession,
    settings: AppSettings,
    actor: RequireActor,
    redis: Annotated[Any, Depends(get_redis)] = None,
) -> list[SessionResponse]:
    ctx = _build_use_case_context(db, settings, redis)
    session_id = uuid.UUID(int=0)  # placeholder

    use_case = ListMySessions(session_repo=ctx["session_repo"])
    sessions = await use_case.execute(
        cmd=ListMySessionsCommand(user_id=actor.actor_id, current_session_id=session_id)
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
# DELETE /auth/sessions/{session_id}
# ---------------------------------------------------------------------------


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_my_session_endpoint(
    session_id: uuid.UUID,
    db: DbSession,
    settings: AppSettings,
    actor: RequireActor,
    redis: Annotated[Any, Depends(get_redis)] = None,
) -> None:
    ctx = _build_use_case_context(db, settings, redis)

    async with db.begin():
        use_case = RevokeSession(
            session_repo=ctx["session_repo"],
            outbox=ctx["outbox"],
        )
        await use_case.execute(
            cmd=RevokeSessionCommand(
                actor_id=actor.actor_id,
                actor_role=actor.role,
                target_user_id=actor.actor_id,
                session_id=session_id,
            )
        )


# ---------------------------------------------------------------------------
# POST /auth/change-password  (two branches)
#
# ADR-0008 D3 / MF-5: two distinct auth lanes depending on the request body:
#   (a) enrollment_token present → forced-enrollment terminal step (anonymous
#       ticket lane, no RequireActor — identity from ticket only).
#   (b) enrollment_token absent → normal path (RequireActor actor-JWT lane,
#       unchanged per INV-2).
#
# We keep RequireActor in scope but treat it as Optional by using the lower-level
# CurrentActor dep.  The endpoint dispatches on enrollment_token presence.
# ---------------------------------------------------------------------------


@router.post("/change-password")
async def change_password_endpoint(
    body: dict[str, Any],
    request: Request,
    db: DbSession,
    settings: AppSettings,
    redis: Annotated[Any, Depends(get_redis)] = None,
    response: Response = None,  # type: ignore[assignment]
) -> LoginSuccessResponse | dict[str, str]:
    """Change password — ticket lane (forced enrollment) or actor-JWT lane (normal path).

    MF-3/D3a.2: Request body MUST NOT be logged on the enrollment branch.
    MF-5: Enrollment branch identity comes from ticket only.
    INV-2: Normal path still requires a valid actor JWT (RequireActor unchanged).
    """

    ctx = _build_use_case_context(db, settings, redis)
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    enrollment_token: str | None = body.get("enrollment_token") if isinstance(body, dict) else None
    new_password: str | None = body.get("new_password") if isinstance(body, dict) else None

    if not new_password:
        raise HTTPException(status_code=422, detail="new_password is required")

    if enrollment_token:
        # ── Branch (a): forced-enrollment terminal step — anonymous ticket lane ──
        # MF-5: identity from ticket only; actor JWT NOT checked/used here.
        # Resolver MUST run INSIDE db.begin() — see confirm_totp_enrollment_endpoint
        # for the same constraint (autobegin transaction would clash otherwise).
        # allow_totp_enrolled=True: by design this terminal step runs AFTER
        # /totp/enroll/confirm has just persisted the TOTP secret (ADR-0008 D5.5
        # forced-change-password chain). The MF-4 "must not be enrolled" check
        # would block this legitimate step; ChangePassword's forced-path guard
        # (user.must_change_password == True) is the actual access control here.
        async with db.begin():
            user_id = await _make_enrollment_ticket_resolver(ctx).execute(
                ticket_id=enrollment_token,
                allow_totp_enrolled=True,
            )
            use_case = ChangePassword(
                user_repo=ctx["user_repo"],
                session_repo=ctx["session_repo"],
                hasher=ctx["hasher"],
                hibp_checker=ctx["hibp_checker"],
                re_mfa_store=ctx["re_mfa_store"],
                jwt_issuer=ctx["jwt_issuer"],
                outbox=ctx["outbox"],
                attempts_repo=ctx["attempts_repo"],
                pending_totp_store=ctx["pending_totp_store"],
                session_ttl_seconds=settings.refresh_token_ttl_seconds,
            )
            result = await use_case.execute(
                cmd=ChangePasswordCommand(
                    user_id=user_id,
                    session_id=uuid.UUID(int=0),  # no existing session
                    new_password=new_password,
                ),
                enrollment_token=enrollment_token,
                ip_address=ip_address,
                user_agent=user_agent,
            )

        if result is not None:
            if result.refresh_token:
                _set_refresh_cookie(response, result.refresh_token, settings)
            return LoginSuccessResponse(
                access_token=result.access_token,
                refresh_token=result.refresh_token,
            )
        return {"detail": "Password changed successfully"}

    else:
        # ── Branch (b): normal path — actor-JWT lane (INV-2 preserved) ──
        # Re-resolve the actor from the header (RequireActor is not listed in the
        # function signature to keep the body param untyped, so we call the dep
        # function directly here to enforce the same gate).
        actor_claims = await current_actor(
            x_internal_token=request.headers.get("X-Internal-Token"),
            settings=settings,
            redis=redis,
        )
        actor_claims = await require_actor(actor_claims)

        session_id = uuid.UUID(int=0)  # placeholder — real sid not yet in internal JWT

        async with db.begin():
            use_case = ChangePassword(
                user_repo=ctx["user_repo"],
                session_repo=ctx["session_repo"],
                hasher=ctx["hasher"],
                hibp_checker=ctx["hibp_checker"],
                re_mfa_store=ctx["re_mfa_store"],
                jwt_issuer=ctx["jwt_issuer"],
                outbox=ctx["outbox"],
                session_ttl_seconds=settings.refresh_token_ttl_seconds,
            )
            result = await use_case.execute(
                cmd=ChangePasswordCommand(
                    user_id=actor_claims.actor_id,
                    session_id=session_id,
                    new_password=new_password,
                ),
                ip_address=ip_address,
                user_agent=user_agent,
            )

        if result is not None:
            if result.refresh_token:
                _set_refresh_cookie(response, result.refresh_token, settings)
            return LoginSuccessResponse(
                access_token=result.access_token,
                refresh_token=result.refresh_token,
            )
        return {"detail": "Password changed successfully"}
