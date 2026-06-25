# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Self-service profile endpoints — /api/v1/auth/me

Endpoints:
  GET   /auth/me                         — return the authenticated user's profile
  PATCH /auth/me                         — update full_name
  POST  /auth/me/change-email/request    — step 1 of email change flow
  POST  /auth/me/change-email/confirm    — step 2 of email change flow

Re-MFA is handled at the BFF layer (TOTP code in the request body is verified
there before forwarding). This service receives an already-verified actor.
"""

from __future__ import annotations

import uuid

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends
from lotsman_shared.actors import ACTOR_OUTBOX_DISPATCHER
from pydantic import BaseModel, Field

from auth_service.api.deps import DbSession, RequireActor, get_redis
from auth_service.api.schemas import (
    CreateSavedFilterRequest,
    SavedFilterResponse,
    UpdateSavedFilterRequest,
    UserResponse,
)
from auth_service.application.dto import (
    ConfirmEmailChangeCommand,
    CreateSavedFilterCommand,
    DeleteSavedFilterCommand,
    EmailChangeConfirmedDTO,
    EmailChangeRequestedDTO,
    GetMyProfileCommand,
    ListMySavedFiltersQuery,
    RequestEmailChangeCommand,
    UpdateMyFullNameCommand,
    UpdateSavedFilterCommand,
    UserDTO,
)
from auth_service.application.use_cases.confirm_email_change import ConfirmEmailChange
from auth_service.application.use_cases.create_saved_filter import CreateSavedFilter
from auth_service.application.use_cases.delete_saved_filter import DeleteSavedFilter
from auth_service.application.use_cases.get_my_profile import GetMyProfile
from auth_service.application.use_cases.list_my_saved_filters import ListMySavedFilters
from auth_service.application.use_cases.request_email_change import RequestEmailChange
from auth_service.application.use_cases.update_my_full_name import UpdateMyFullName
from auth_service.application.use_cases.update_saved_filter import UpdateSavedFilter
from auth_service.config import get_settings
from auth_service.domain.entities import TOTP_SENTINEL
from auth_service.infrastructure.db.repositories import (
    SqlaEventOutbox,
    SqlaSavedFilterRepository,
    SqlaUserRepository,
)
from auth_service.infrastructure.notification_email_adapter import (
    NotificationServiceEmailAdapter,
)
from auth_service.infrastructure.password import Argon2PasswordHasher
from auth_service.infrastructure.redis.email_change_store import RedisEmailChangeStore
from auth_service.infrastructure.redis.lockout_store import RedisLockoutStore

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class UpdateMeRequest(BaseModel):
    """Request body for PATCH /auth/me."""

    full_name: str = Field(..., min_length=1, max_length=200)
    # Optional self-service UI preference. None = leave unchanged. Bounds mirror
    # the DB CHECK and domain constants; the use case re-validates defensively.
    ui_font_scale: int | None = Field(None, ge=80, le=150)


class ChangeEmailRequestBody(BaseModel):
    """Request body for POST /auth/me/change-email/request.

    The TOTP code in this body is verified at the BFF layer before the request
    is forwarded here. This endpoint does NOT re-verify TOTP — it trusts the
    authenticated actor (already verified by the internal-JWT gate).
    """

    new_email: str = Field(..., description="Desired new email address")
    totp_code: str = Field(
        ...,
        description="Current TOTP code — verified at BFF layer; included here for BFF forwarding convenience",
    )


class ChangeEmailConfirmBody(BaseModel):
    """Request body for POST /auth/me/change-email/confirm."""

    request_id: str = Field(..., description="request_id returned by the /request endpoint")
    verification_code: str = Field(..., description="8-digit code sent to the new email address")


async def _resolve_flags(
    user_repo: SqlaUserRepository,
    lockout_store: RedisLockoutStore,
    actor_id: uuid.UUID,
) -> tuple[bool, bool]:
    """Return (totp_enrolled, is_locked) for the given actor."""
    user = await user_repo.get_by_id(actor_id)
    totp_enrolled = (user.totp_secret_enc != TOTP_SENTINEL) if user else False
    is_locked = await lockout_store.is_locked(actor_id)
    return totp_enrolled, is_locked


def _to_user_response(dto: UserDTO, *, totp_enrolled: bool, is_locked: bool) -> UserResponse:
    return UserResponse(
        id=dto.id,
        email=dto.email,
        full_name=dto.full_name,
        role=dto.role,
        is_active=dto.is_active,
        must_change_password=dto.must_change_password,
        last_login_at=dto.last_login_at,
        created_at=dto.created_at,
        updated_at=dto.updated_at,
        totp_enrolled=totp_enrolled,
        is_locked=is_locked,
        ui_font_scale=dto.ui_font_scale,
    )


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------


@router.get("/me")
async def get_my_profile_endpoint(
    db: DbSession,
    actor: RequireActor,
    redis: aioredis.Redis = Depends(get_redis),
) -> UserResponse:
    """Return the authenticated user's full profile.

    Includes totp_enrolled and is_locked so the SPA profile page can render
    all relevant status fields without a separate admin-only lookup.
    """
    user_repo = SqlaUserRepository(db)
    lockout_store = RedisLockoutStore(redis)

    use_case = GetMyProfile(user_repo=user_repo)
    dto = await use_case.execute(cmd=GetMyProfileCommand(actor_id=actor.actor_id))

    totp_enrolled, is_locked = await _resolve_flags(user_repo, lockout_store, actor.actor_id)

    log.info("profile_fetched", actor_id=str(actor.actor_id))
    return _to_user_response(dto, totp_enrolled=totp_enrolled, is_locked=is_locked)


# ---------------------------------------------------------------------------
# PATCH /auth/me
# ---------------------------------------------------------------------------


@router.patch("/me")
async def update_my_profile_endpoint(
    body: UpdateMeRequest,
    db: DbSession,
    actor: RequireActor,
    redis: aioredis.Redis = Depends(get_redis),
) -> UserResponse:
    """Update the authenticated user's full_name.

    email is read-only from the user's perspective — it is the identity key
    managed by administrators via the whitelist. There is no endpoint for
    self-service email change.
    """
    user_repo = SqlaUserRepository(db)
    outbox = SqlaEventOutbox(db)
    lockout_store = RedisLockoutStore(redis)

    async with db.begin():
        use_case = UpdateMyFullName(user_repo=user_repo, outbox=outbox)
        dto = await use_case.execute(
            cmd=UpdateMyFullNameCommand(
                actor_id=actor.actor_id,
                full_name=body.full_name,
                ui_font_scale=body.ui_font_scale,
            )
        )

    # Resolve dynamic flags after commit (read-only Redis queries).
    totp_enrolled, is_locked = await _resolve_flags(user_repo, lockout_store, actor.actor_id)

    log.info(
        "profile_updated",
        actor_id=str(actor.actor_id),
        field="full_name",
    )
    return _to_user_response(dto, totp_enrolled=totp_enrolled, is_locked=is_locked)


# ---------------------------------------------------------------------------
# POST /auth/me/change-email/request
# ---------------------------------------------------------------------------


@router.post("/me/change-email/request")
async def request_email_change_endpoint(
    body: ChangeEmailRequestBody,
    db: DbSession,
    actor: RequireActor,
    redis: aioredis.Redis = Depends(get_redis),
) -> EmailChangeRequestedDTO:
    """Step 1: request an email change — generates and emails a verification code.

    TOTP re-MFA is enforced at the BFF layer. This endpoint trusts the actor.
    Returns masked_new_email so the UI can display confirmation. The raw
    verification code is delivered to new_email ONLY — never returned here.
    """
    settings = get_settings()
    user_repo = SqlaUserRepository(db)
    email_change_store = RedisEmailChangeStore(redis)
    hasher = Argon2PasswordHasher()
    outbox = SqlaEventOutbox(db)

    email_sender = NotificationServiceEmailAdapter(
        notification_svc_url=settings.notification_svc_url,
        signing_key=settings.internal_jwt_key_notification,
        system_actor_id=ACTOR_OUTBOX_DISPATCHER,
    )

    async with db.begin():
        use_case = RequestEmailChange(
            user_repo=user_repo,
            email_change_store=email_change_store,
            hasher=hasher,
            email_sender=email_sender,
            outbox=outbox,
        )
        dto = await use_case.execute(
            cmd=RequestEmailChangeCommand(
                actor_id=actor.actor_id,
                new_email=body.new_email,
            )
        )

    return dto


# ---------------------------------------------------------------------------
# POST /auth/me/change-email/confirm
# ---------------------------------------------------------------------------


@router.post("/me/change-email/confirm")
async def confirm_email_change_endpoint(
    body: ChangeEmailConfirmBody,
    db: DbSession,
    actor: RequireActor,
    redis: aioredis.Redis = Depends(get_redis),
) -> EmailChangeConfirmedDTO:
    """Step 2: confirm the email change by supplying the 8-digit verification code.

    No re-MFA required — the verification code itself is the second factor.

    On success: updates auth.users.email and returns {email: new_email}.
    Note: existing access JWTs carry the old email as a claim. Sessions are NOT
    invalidated — the next token refresh (within 15 min max) will carry the new
    email. This is the documented trade-off for this flow.
    """
    user_repo = SqlaUserRepository(db)
    email_change_store = RedisEmailChangeStore(redis)
    hasher = Argon2PasswordHasher()
    outbox = SqlaEventOutbox(db)

    async with db.begin():
        use_case = ConfirmEmailChange(
            user_repo=user_repo,
            email_change_store=email_change_store,
            hasher=hasher,
            outbox=outbox,
        )
        dto = await use_case.execute(
            cmd=ConfirmEmailChangeCommand(
                actor_id=actor.actor_id,
                request_id=body.request_id,
                verification_code=body.verification_code,
            )
        )

    log.info(
        "email_change_confirmed",
        actor_id=str(actor.actor_id),
        request_id=body.request_id,
    )
    return dto


# ---------------------------------------------------------------------------
# SavedFilter CRUD  (v1.23.0 — registry-filters feature)
# GET    /auth/me/saved-filters
# POST   /auth/me/saved-filters
# PATCH  /auth/me/saved-filters/{filter_id}
# DELETE /auth/me/saved-filters/{filter_id}
# ---------------------------------------------------------------------------


def _sf_response(dto: "SavedFilterDTO") -> SavedFilterResponse:  # type: ignore[name-defined]  # noqa: F821
    from auth_service.application.dto import SavedFilterDTO as _DTO

    assert isinstance(dto, _DTO)
    return SavedFilterResponse(
        id=dto.id,
        user_id=dto.user_id,
        name=dto.name,
        filter_json=dto.filter_json,
        is_default=dto.is_default,
        created_at=dto.created_at,
        updated_at=dto.updated_at,
    )


@router.get("/me/saved-filters", response_model=list[SavedFilterResponse])
async def list_saved_filters(
    db: DbSession,
    actor: RequireActor,
) -> list[SavedFilterResponse]:
    """Return all named filter presets for the authenticated user.

    Results are ordered: default preset first, then alphabetically by name.
    """
    uc = ListMySavedFilters(repo=SqlaSavedFilterRepository(db))
    dtos = await uc.execute(query=ListMySavedFiltersQuery(user_id=actor.actor_id))
    log.info("saved_filters_listed", actor_id=str(actor.actor_id), count=len(dtos))
    return [_sf_response(d) for d in dtos]


@router.post("/me/saved-filters", response_model=SavedFilterResponse, status_code=201)
async def create_saved_filter(
    body: CreateSavedFilterRequest,
    db: DbSession,
    actor: RequireActor,
) -> SavedFilterResponse:
    """Save a new named filter preset.

    At most 20 presets per user. Name must be unique per user.
    is_default=true replaces any existing default.
    """
    async with db.begin():
        uc = CreateSavedFilter(
            repo=SqlaSavedFilterRepository(db),
            outbox=SqlaEventOutbox(db),
        )
        dto = await uc.execute(
            cmd=CreateSavedFilterCommand(
                user_id=actor.actor_id,
                name=body.name,
                filter_json=body.filter_json,
                is_default=body.is_default,
            )
        )

    log.info("saved_filter_created", actor_id=str(actor.actor_id), filter_id=str(dto.id))
    return _sf_response(dto)


@router.patch(
    "/me/saved-filters/{filter_id}",
    response_model=SavedFilterResponse,
)
async def update_saved_filter(
    filter_id: uuid.UUID,
    body: UpdateSavedFilterRequest,
    db: DbSession,
    actor: RequireActor,
) -> SavedFilterResponse:
    """Partially update a named filter preset (any or all fields)."""
    async with db.begin():
        uc = UpdateSavedFilter(
            repo=SqlaSavedFilterRepository(db),
            outbox=SqlaEventOutbox(db),
        )
        dto = await uc.execute(
            cmd=UpdateSavedFilterCommand(
                user_id=actor.actor_id,
                filter_id=filter_id,
                name=body.name,
                filter_json=body.filter_json,
                is_default=body.is_default,
            )
        )

    log.info("saved_filter_updated", actor_id=str(actor.actor_id), filter_id=str(filter_id))
    return _sf_response(dto)


@router.delete("/me/saved-filters/{filter_id}", status_code=204)
async def delete_saved_filter(
    filter_id: uuid.UUID,
    db: DbSession,
    actor: RequireActor,
) -> None:
    """Hard-delete a named filter preset. Returns 204 No Content."""
    async with db.begin():
        uc = DeleteSavedFilter(
            repo=SqlaSavedFilterRepository(db),
            outbox=SqlaEventOutbox(db),
        )
        await uc.execute(
            cmd=DeleteSavedFilterCommand(
                user_id=actor.actor_id,
                filter_id=filter_id,
            )
        )

    log.info("saved_filter_deleted", actor_id=str(actor.actor_id), filter_id=str(filter_id))
