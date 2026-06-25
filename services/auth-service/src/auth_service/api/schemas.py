# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Pydantic request/response schemas for the auth-service API.

SECURITY: These schemas NEVER expose password hashes, TOTP secrets, or
raw refresh tokens. Schema-level redaction is enforced by field selection.

Mapping: API schemas <-> application DTOs at the api/ boundary.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Auth flow request schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str = Field(..., description="User email address")
    password: str = Field(..., min_length=1, max_length=1024)
    totp_code: str | None = Field(None, description="6-digit TOTP code (if enrolled)")
    backup_code: str | None = Field(
        None,
        pattern=r"^[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}$",
        description="Backup code in XXXX-XXXX format",
    )


class VerifyTotpRequest(BaseModel):
    session_ticket: str = Field(..., description="Opaque ticket from login phase 1")
    totp_code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class ChangePasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=12, max_length=1024)


class EnrollTotpEnrollmentRequest(BaseModel):
    """POST /auth/totp/enroll — enrollment-ticket body (ADR-0008 D3 / MF-5).

    Validation errors MUST NOT echo enrollment_token (D3a.4 / MF-3).
    hide_input_in_errors=True prevents Pydantic v2 from including the submitted
    value in the 422 response body (ADR-0008 D3a.4 / MF-3 fix).
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    enrollment_token: str = Field(..., min_length=1, max_length=512)


class ConfirmTotpEnrollmentEnrollmentRequest(BaseModel):
    """POST /auth/totp/enroll/confirm — enrollment-ticket + TOTP code (ADR-0008 D3).

    Validation errors MUST NOT echo enrollment_token or code (D3a.4 / MF-3).
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    enrollment_token: str = Field(..., min_length=1, max_length=512)
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class ChangePasswordEnrollmentRequest(BaseModel):
    """POST /auth/change-password — forced-enrollment branch (ADR-0008 D3 / MF-5).

    Validation errors MUST NOT echo enrollment_token or new_password (D3a.4 / MF-3).
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    enrollment_token: str = Field(..., min_length=1, max_length=512)
    new_password: str = Field(..., min_length=12, max_length=1024)


class ReMfaCheckRequest(BaseModel):
    totp_code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class TotpEnrollConfirmRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class BackupCodeVerifyRequest(BaseModel):
    session_ticket: str
    backup_code: str = Field(..., pattern=r"^[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}$")


# ---------------------------------------------------------------------------
# Admin request schemas
# ---------------------------------------------------------------------------


class CreateUserRequest(BaseModel):
    email: str
    full_name: str = Field(..., min_length=1)
    role: str = Field(..., pattern="^(super_admin|admin|editor|viewer)$")


class InviteUserRequest(BaseModel):
    """POST /admin/users — create user with delivery mode (ADR-0004 Phase 2b)."""

    email: str
    full_name: str = Field(..., min_length=1)
    role: str = Field(..., pattern="^(super_admin|admin|editor|viewer)$")
    delivery: str = Field(default="show-otp", pattern="^(auto|show-otp)$")


class ReInviteUserRequest(BaseModel):
    """POST /admin/users/{id}/invite — re-invite pending user (US-10)."""

    delivery: str = Field(default="auto", pattern="^(auto|show-otp)$")


class ChangeRoleRequest(BaseModel):
    role: str = Field(..., pattern="^(super_admin|admin|editor|viewer)$")


class AdminUpdateUserProfileRequest(BaseModel):
    """US-103. Admin updates target user's profile. Currently only full_name."""

    full_name: str = Field(..., min_length=1, max_length=200)


class ResetTotpAdminRequest(BaseModel):
    admin_totp_code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class LoginPendingTotpResponse(BaseModel):
    next_step: str = "verify_totp"
    session_ticket: str


class LoginPendingEnrollResponse(BaseModel):
    next_step: str = "enroll_totp"
    enrollment_token: str


class LoginSuccessResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    backup_codes_warning: int | None = None
    # Internal API response field — auth-svc returns the plaintext refresh
    # token in the JSON body (not as Set-Cookie). The web-bff reads it from
    # body and sets the HttpOnly cookie with browser-appropriate attributes
    # (Secure/SameSite from env, configurable for dev vs prod). This keeps
    # auth-svc as a pure microservice unaware of cookie semantics.
    refresh_token: str | None = None


class TotpEnrollResponse(BaseModel):
    secret_b32: str
    otpauth_url: str


class TotpConfirmResponse(BaseModel):
    backup_codes: list[str]
    # Optional — present only on the enroll-only terminal branch (ADR-0008 D5.4).
    # When must_change_password=False, enroll/confirm is the terminal step and
    # returns a real access+refresh pair so the SPA can authenticate immediately.
    access_token: str | None = None
    refresh_token: str | None = None


class BackupCodesRegeneratedResponse(BaseModel):
    backup_codes: list[str]


class SessionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_agent: str | None
    ip_address: str | None
    created_at: datetime
    expires_at: datetime
    is_current: bool = False


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Blocker 6: required by UsersPage to determine isPendingUser and show lockout badge.
    totp_enrolled: bool = False
    is_locked: bool = False
    # Per-user web-interface font-size preference (percent of base, 100 = default).
    ui_font_scale: int = 100


class CreateUserResponse(BaseModel):
    user_id: uuid.UUID
    oob_otp: str


class InviteUserAutoResponse(BaseModel):
    """Returned when delivery='auto'. OTP is NOT in body."""

    user_id: uuid.UUID
    channel_used: str
    invitation_id: uuid.UUID


class InviteUserShowOtpResponse(BaseModel):
    """Returned when delivery='show-otp'. Shown ONCE in UI modal."""

    user_id: uuid.UUID
    otp: str
    otp_ttl_minutes: int = 10


class PasswordResetResponse(BaseModel):
    oob_otp: str


class RevokeAllSessionsResponse(BaseModel):
    revoked_count: int


# ---------------------------------------------------------------------------
# SavedFilter schemas (v1.23.0 — registry-filters feature)
# ---------------------------------------------------------------------------


class SavedFilterResponse(BaseModel):
    """Single named filter preset returned by the API."""

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    filter_json: dict[str, Any]
    is_default: bool
    created_at: datetime
    updated_at: datetime


class CreateSavedFilterRequest(BaseModel):
    """POST /api/v1/auth/me/saved-filters body."""

    name: str = Field(..., min_length=1, max_length=100)
    filter_json: dict[str, Any] = Field(
        ...,
        description="Arbitrary JSON object owned by the frontend filter state",
    )
    is_default: bool = Field(default=False)


class UpdateSavedFilterRequest(BaseModel):
    """PATCH /api/v1/auth/me/saved-filters/{id} body — all fields optional."""

    name: str | None = Field(None, min_length=1, max_length=100)
    filter_json: dict[str, Any] | None = None
    is_default: bool | None = None


class ReMfaCheckResponse(BaseModel):
    mfa_verified: bool = True
