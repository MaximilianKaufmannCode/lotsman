# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Application-layer DTOs for auth-service.

Pure Pydantic v2 data classes — no I/O, no SQLAlchemy.
These are the inputs (Commands) and outputs returned by use cases.
Mapping: API schemas -> DTOs -> domain entities (at each boundary).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from auth_service.domain.entities import SavedFilter, Session, User

# ---------------------------------------------------------------------------
# Commands (use-case inputs)
# ---------------------------------------------------------------------------


class CreateUserCommand(BaseModel):
    email: str = Field(..., description="New user email (lowercased at boundary)")
    full_name: str = Field(..., min_length=1)
    role: str = Field(..., pattern="^(super_admin|admin|editor|viewer)$")
    actor_id: uuid.UUID


class DeactivateUserCommand(BaseModel):
    target_user_id: uuid.UUID
    actor_id: uuid.UUID


class ReactivateUserCommand(BaseModel):
    target_user_id: uuid.UUID
    actor_id: uuid.UUID


class DeleteUserCommand(BaseModel):
    target_user_id: uuid.UUID
    actor_id: uuid.UUID


class AdminUpdateUserProfileCommand(BaseModel):
    """US-103: admin updates another user's profile field (full_name).

    Email change is intentionally NOT supported here — that needs the same
    verification flow as self-service email change (separate ADR).
    """

    target_user_id: uuid.UUID
    actor_id: uuid.UUID
    full_name: str


class ChangeRoleCommand(BaseModel):
    target_user_id: uuid.UUID
    new_role: str = Field(..., pattern="^(super_admin|admin|editor|viewer)$")
    actor_id: uuid.UUID


class StartLoginCommand(BaseModel):
    email: str
    password: str = Field(..., min_length=1, max_length=1024)
    totp_code: str | None = None
    backup_code: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None


class VerifyTotpCommand(BaseModel):
    ticket_id: str  # opaque pending-login ticket
    totp_code: str
    ip_address: str | None = None
    user_agent: str | None = None


class VerifyBackupCodeCommand(BaseModel):
    ticket_id: str
    backup_code: str
    ip_address: str | None = None
    user_agent: str | None = None


class EnrollTotpCommand(BaseModel):
    user_id: uuid.UUID


class ConfirmTotpEnrollmentCommand(BaseModel):
    user_id: uuid.UUID
    code: str


class RegenerateBackupCodesCommand(BaseModel):
    user_id: uuid.UUID
    session_id: uuid.UUID  # for re-MFA check


class ChangePasswordCommand(BaseModel):
    user_id: uuid.UUID
    session_id: uuid.UUID
    new_password: str = Field(..., min_length=12, max_length=1024)


class ResetPasswordAdminCommand(BaseModel):
    actor_id: uuid.UUID  # admin
    target_user_id: uuid.UUID


class LogoutCommand(BaseModel):
    refresh_token: str | None = None  # may be absent (idempotent)


class RefreshTokensCommand(BaseModel):
    refresh_token: str
    ip_address: str | None = None
    user_agent: str | None = None


class RevokeSessionCommand(BaseModel):
    actor_id: uuid.UUID
    actor_role: str
    target_user_id: uuid.UUID
    session_id: uuid.UUID


class RevokeAllSessionsCommand(BaseModel):
    actor_id: uuid.UUID
    target_user_id: uuid.UUID


class LockoutUserAdminCommand(BaseModel):
    actor_id: uuid.UUID
    target_user_id: uuid.UUID


class UnlockUserAdminCommand(BaseModel):
    actor_id: uuid.UUID
    target_user_id: uuid.UUID


class ResetTotpAdminCommand(BaseModel):
    actor_id: uuid.UUID
    target_user_id: uuid.UUID
    admin_totp_code: str


class ListMySessionsCommand(BaseModel):
    user_id: uuid.UUID
    current_session_id: uuid.UUID


class ListUserSessionsAdminCommand(BaseModel):
    actor_id: uuid.UUID
    target_user_id: uuid.UUID


class ReMfaCheckCommand(BaseModel):
    user_id: uuid.UUID
    session_id: uuid.UUID
    totp_code: str
    ip_address: str | None = None


class RecordLoginAttemptCommand(BaseModel):
    email: str
    outcome: str
    ip_address: str | None = None
    user_agent: str | None = None


# ---------------------------------------------------------------------------
# Result DTOs
# ---------------------------------------------------------------------------


class UserDTO(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, user: User) -> UserDTO:
        from auth_service.domain.entities import User as _User

        assert isinstance(user, _User)
        return cls(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
            must_change_password=user.must_change_password,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )


class SessionDTO(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_agent: str | None
    ip_address: str | None
    created_at: datetime
    expires_at: datetime
    is_current: bool = False

    @classmethod
    def from_entity(
        cls, session: Session, *, current_session_id: uuid.UUID | None = None
    ) -> SessionDTO:
        from auth_service.domain.entities import Session as _Session

        assert isinstance(session, _Session)
        return cls(
            id=session.id,
            user_id=session.user_id,
            user_agent=session.user_agent,
            ip_address=str(session.ip_address) if session.ip_address is not None else None,
            created_at=session.created_at,
            expires_at=session.expires_at,
            is_current=(current_session_id is not None and session.id == current_session_id),
        )


class LoginPendingTotpDTO(BaseModel):
    """Returned by StartLogin when password was correct but TOTP not yet provided."""

    next_step: str = "verify_totp"
    session_ticket: str  # opaque, pass back to VerifyTotp


class LoginPendingEnrollDTO(BaseModel):
    """Returned by StartLogin (OOB OTP path) — user must enroll TOTP."""

    next_step: str = "enroll_totp"
    enrollment_token: str  # scoped token for /enroll and /change-password only


class LoginSuccessDTO(BaseModel):
    """Full successful login: access JWT + opaque refresh token."""

    access_token: str
    token_type: str = "Bearer"
    refresh_token: str  # plaintext; BFF will set as HttpOnly cookie
    backup_codes_warning: int | None = None  # ≤2 remaining → warn


class TotpEnrollDTO(BaseModel):
    secret_b32: str
    otpauth_url: str


class TotpConfirmDTO(BaseModel):
    backup_codes: list[str]  # 10 codes shown once in format XXXX-XXXX


class ConfirmTotpEnrollmentTerminalDTO(BaseModel):
    """Returned by ConfirmTotpEnrollment on the enroll-only terminal branch (ADR-0008 D5.4.9).

    Carries backup_codes (plaintext, shown once) plus access/refresh tokens so the
    SPA can authenticate immediately without a second round-trip.
    Kept separate from LoginSuccessDTO to avoid optional-field creep (reviewer MN-3).
    """

    backup_codes: list[str]  # 10 plaintext codes, shown once
    access_token: str
    refresh_token: str


class BackupCodesRegeneratedDTO(BaseModel):
    backup_codes: list[str]  # 10 new codes


class PasswordResetDTO(BaseModel):
    oob_otp: str  # plaintext OTP; admin delivers out-of-band


class RevokeAllSessionsDTO(BaseModel):
    revoked_count: int


class ReMfaResultDTO(BaseModel):
    mfa_verified: bool = True


class CreateUserDTO(BaseModel):
    user_id: uuid.UUID
    oob_otp: str  # plaintext; admin delivers out-of-band


# ---------------------------------------------------------------------------
# Bootstrap admin (ADR-0004 §3 / Phase 1)
# ---------------------------------------------------------------------------


class BootstrapAdminCommand(BaseModel):
    email: str = Field(..., description="Email for the admin user")
    full_name: str = Field(..., min_length=1)


class BootstrapAdminDTO(BaseModel):
    """Result of a successful bootstrap.  oob_otp MUST NOT appear in logs."""

    user_id: uuid.UUID
    email: str
    oob_otp: str  # plaintext; print to stdout ONLY — never log


# ---------------------------------------------------------------------------
# Bootstrap super_admin (ADR-0006 Phase 1)
# ---------------------------------------------------------------------------


class BootstrapSuperAdminCommand(BaseModel):
    """Input to BootstrapSuperAdmin use case.

    IMPORTANT: This command must NEVER be accepted by the bootstrap_admin CLI
    or make admin-create.  Only make superadmin-create invokes
    bootstrap_super_admin.py.
    """

    email: str = Field(..., description="Email for the super_admin user")
    full_name: str = Field(..., min_length=1)


class BootstrapSuperAdminDTO(BaseModel):
    """Result of a successful super_admin bootstrap. oob_otp MUST NOT appear in logs."""

    user_id: uuid.UUID
    email: str
    oob_otp: str  # plaintext; print to stdout ONLY — never log


# ---------------------------------------------------------------------------
# InviteUser (ADR-0004 Phase 2b)
# ---------------------------------------------------------------------------


class InviteUserCommand(BaseModel):
    email: str = Field(..., description="New user email")
    full_name: str = Field(..., min_length=1)
    role: str = Field(..., pattern="^(super_admin|admin|editor|viewer)$")
    delivery: str = Field(..., pattern="^(auto|show-otp)$")
    actor_id: uuid.UUID
    login_url: str = Field(default="https://lotsman.example.com")


class InviteUserAutoDTO(BaseModel):
    """Returned when delivery='auto'."""

    user_id: uuid.UUID
    channel_used: str
    invitation_id: uuid.UUID


class InviteUserShowOtpDTO(BaseModel):
    """Returned when delivery='show-otp'."""

    user_id: uuid.UUID
    otp: str  # plaintext — shown ONCE in UI; never log
    otp_ttl_minutes: int = 10


# ---------------------------------------------------------------------------
# ReInviteUser (US-10)
# ---------------------------------------------------------------------------


class ReInviteUserCommand(BaseModel):
    target_user_id: uuid.UUID
    actor_id: uuid.UUID
    delivery: str = Field(default="auto", pattern="^(auto|show-otp)$")
    login_url: str = Field(default="https://lotsman.example.com")


# ---------------------------------------------------------------------------
# Profile self-service commands (user-facing — not admin)
# ---------------------------------------------------------------------------


class GetMyProfileCommand(BaseModel):
    actor_id: uuid.UUID


class UpdateMyFullNameCommand(BaseModel):
    actor_id: uuid.UUID
    # No Pydantic length validation here — the use case validates and raises
    # ProfileValidationError (typed domain error) so callers get the right HTTP 422.
    full_name: str


class RequestEmailChangeCommand(BaseModel):
    """Input to RequestEmailChange use case."""

    actor_id: uuid.UUID
    new_email: str


class ConfirmEmailChangeCommand(BaseModel):
    """Input to ConfirmEmailChange use case."""

    actor_id: uuid.UUID
    request_id: str
    verification_code: str


class EmailChangeRequestedDTO(BaseModel):
    """Returned by RequestEmailChange on success."""

    request_id: str
    code_ttl_seconds: int
    masked_new_email: str


class EmailChangeConfirmedDTO(BaseModel):
    """Returned by ConfirmEmailChange on success."""

    email: str


# ---------------------------------------------------------------------------
# SavedFilter (v1.23.0 — registry-filters feature)
# ---------------------------------------------------------------------------


class CreateSavedFilterCommand(BaseModel):
    """Input to CreateSavedFilter use case."""

    user_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=100)
    filter_json: dict  # type: ignore[type-arg]
    is_default: bool = False


class UpdateSavedFilterCommand(BaseModel):
    """Input to UpdateSavedFilter use case (all payload fields are optional — partial update)."""

    user_id: uuid.UUID
    filter_id: uuid.UUID
    name: str | None = Field(None, min_length=1, max_length=100)
    filter_json: dict | None = None  # type: ignore[type-arg]
    is_default: bool | None = None


class DeleteSavedFilterCommand(BaseModel):
    user_id: uuid.UUID
    filter_id: uuid.UUID


class ListMySavedFiltersQuery(BaseModel):
    user_id: uuid.UUID


class SavedFilterDTO(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    filter_json: dict  # type: ignore[type-arg]
    is_default: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, sf: SavedFilter) -> SavedFilterDTO:  # type: ignore[name-defined]
        from auth_service.domain.entities import SavedFilter as _SF

        assert isinstance(sf, _SF)
        return cls(
            id=sf.id,
            user_id=sf.user_id,
            name=sf.name,
            filter_json=sf.filter_json,
            is_default=sf.is_default,
            created_at=sf.created_at,
            updated_at=sf.updated_at,
        )
