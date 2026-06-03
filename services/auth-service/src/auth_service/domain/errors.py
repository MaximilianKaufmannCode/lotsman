# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Typed domain errors for auth-service.

All errors inherit from DomainError (lotsman_shared.errors) so the API layer
exception handler can translate them to HTTP responses without the domain
layer knowing about HTTP status codes.

IMPORTANT: InvalidCredentialsError is the UNIFORM error for all authentication
failures (wrong password, wrong TOTP, locked, deactivated, no user). The API
exception handler maps ALL of these to HTTP 401 {"detail": "Invalid credentials"}
to prevent user enumeration (ADR-0003 §12, US-2 Gherkin).
"""

from __future__ import annotations

from lotsman_shared.errors import DomainError


class AuthDomainError(DomainError):
    """Base for all auth-service domain errors."""


# ---------------------------------------------------------------------------
# UNIFORM 401 errors (anti-enumeration — all return same HTTP response)
# ---------------------------------------------------------------------------


class InvalidCredentialsError(AuthDomainError):
    """Raised for: wrong password, wrong TOTP, locked account, deactivated account,
    non-existent email, invalid backup code. All map to HTTP 401 with the
    SAME response body: {"detail": "Invalid credentials"}
    """

    status_code = 401
    default_message = "Invalid credentials"


class AccountLockedError(InvalidCredentialsError):
    """The email is locked due to too many failed attempts."""

    status_code = 401
    default_message = "Invalid credentials"


class AccountDeactivatedError(InvalidCredentialsError):
    """The user account is not active."""

    status_code = 401
    default_message = "Invalid credentials"


class TotpInvalidError(InvalidCredentialsError):
    """Invalid or expired TOTP code."""

    status_code = 401
    default_message = "Invalid credentials"


class BackupCodeInvalidError(InvalidCredentialsError):
    """Invalid, used, or unknown backup code."""

    status_code = 401
    default_message = "Invalid credentials"


class SystemActorAccessDeniedError(InvalidCredentialsError):
    """Attempt to log in as a system actor."""

    status_code = 401
    default_message = "Invalid credentials"


# ---------------------------------------------------------------------------
# Other typed errors
# ---------------------------------------------------------------------------


class UserNotFoundError(AuthDomainError):
    status_code = 404
    default_message = "User not found"


class UserAlreadyExistsError(AuthDomainError):
    status_code = 409
    default_message = "A user with this email already exists"


class SessionNotFoundError(AuthDomainError):
    status_code = 404
    default_message = "Session not found"


class SessionExpiredError(AuthDomainError):
    status_code = 401
    default_message = "Session expired"


class SessionRevokedError(AuthDomainError):
    status_code = 401
    default_message = "Session has been revoked"


class RoleForbiddenError(AuthDomainError):
    """Actor's role is insufficient for the requested operation."""

    status_code = 403
    default_message = "Forbidden"


class SystemAccountProtectedError(AuthDomainError):
    """Attempt to deactivate / delete / reactivate a built-in system account."""

    status_code = 403
    code = "SYSTEM_ACCOUNT_PROTECTED"
    default_message = "Системные служебные учётные записи изменять нельзя"


class ReMfaRequiredError(AuthDomainError):
    """The endpoint requires a fresh TOTP confirmation (re-MFA)."""

    status_code = 403
    code = "REMFA_REQUIRED"
    default_message = "Re-authentication required"


class ReMfaFailedError(AuthDomainError):
    """Admin supplied wrong TOTP code for re-MFA gate."""

    status_code = 403
    default_message = "Re-authentication failed"


class PasswordPolicyViolationError(AuthDomainError):
    """New password violates the NIST 800-63B policy (too short, too long, breached)."""

    status_code = 400
    default_message = "Password does not meet policy requirements"


class WeakPasswordError(AuthDomainError):
    """New password found in HIBP breached-password list."""

    status_code = 400
    default_message = "Password appears in known breached password lists"


class TotpEnrollmentExpiredError(AuthDomainError):
    """Pending TOTP enrollment Redis key has expired (TTL > 5 min)."""

    status_code = 400
    default_message = "Enrollment session expired; please restart enrollment"


class TotpCodeAlreadyUsedError(AuthDomainError):
    """TOTP code has already been used within this period_index (anti-replay)."""

    status_code = 400
    code = "REMFA_REPLAY"
    default_message = "TOTP code already used"


class TotpNotEnrolledError(AuthDomainError):
    """User has not enrolled TOTP yet (totp_secret_enc == sentinel)."""

    status_code = 400
    default_message = "TOTP not enrolled"


class SelfActionForbiddenError(AuthDomainError):
    """Admin attempted to perform a forbidden action on their own account."""

    status_code = 400
    default_message = "Cannot perform this action on your own account"


class LastAdminError(AuthDomainError):
    """Deactivating or demoting would leave 0 active admins."""

    status_code = 400
    code = "MIN_ADMINS"
    default_message = "Cannot deactivate: no remaining active admin accounts"


class DeactivatedUserOperationError(AuthDomainError):
    """Cannot perform operation on a deactivated user."""

    status_code = 400
    default_message = "Cannot perform this operation on a deactivated user"


# ---------------------------------------------------------------------------
# Channel / invite errors (ADR-0004 Phase 2b)
# ---------------------------------------------------------------------------


class NoEnabledChannelError(AuthDomainError):
    """delivery='auto' requested but no notification channel is enabled."""

    status_code = 409
    code = "NO_CHANNEL"
    default_message = (
        "Нет включённых каналов — настройте /admin/channels или используйте delivery=show-otp"
    )


class UserAlreadyActivatedError(AuthDomainError):
    """Re-invite attempted on a user who has already completed TOTP enrollment."""

    status_code = 409
    code = "USER_ACTIVATED"
    default_message = "Пользователь уже активирован — используйте сброс пароля"


class MinAdminsViolationError(AuthDomainError):
    """Operation would reduce active admin count below 1."""

    status_code = 409
    code = "MIN_ADMINS"
    default_message = "Должен оставаться минимум 1 активный admin"


class MinSuperAdminsViolationError(AuthDomainError):
    """Operation would reduce active super_admin count below 1."""

    status_code = 409
    code = "MIN_SUPER_ADMINS"
    default_message = "Должен оставаться минимум 1 активный super_admin"


class ProfileValidationError(AuthDomainError):
    """Raised when the user's profile update contains invalid data.

    E.g. full_name is empty or exceeds 200 characters.
    """

    status_code = 422
    code = "PROFILE_VALIDATION"
    default_message = "Profile data is invalid"


# ---------------------------------------------------------------------------
# Email-change errors
# ---------------------------------------------------------------------------


class EmailSameAsCurrentError(AuthDomainError):
    """New email is identical to the current email."""

    status_code = 422
    code = "EMAIL_SAME"
    default_message = "New email must be different from the current email"


class EmailAlreadyTakenError(AuthDomainError):
    """New email is already registered to another active user."""

    status_code = 409
    code = "EMAIL_ALREADY_TAKEN"
    default_message = "This email address is already in use"


class EmailValidationError(AuthDomainError):
    """New email fails format validation."""

    status_code = 422
    code = "EMAIL_VALIDATION"
    default_message = "Invalid email address format"


class EmailChannelNotConfiguredError(AuthDomainError):
    """Email channel is not configured — cannot send verification code."""

    status_code = 503
    code = "EMAIL_CHANNEL_REQUIRED"
    default_message = "Смена email требует работающего email-канала. Обратитесь к администратору."


class EmailChangeRequestNotFoundError(AuthDomainError):
    """No pending email-change request for the given request_id / actor."""

    status_code = 404
    code = "EMAIL_CHANGE_REQUEST_NOT_FOUND"
    default_message = "Email change request not found or expired"


class EmailChangeVerificationFailedError(AuthDomainError):
    """Verification code is wrong. attempts_remaining is attached as detail."""

    status_code = 401
    code = "VERIFICATION_FAILED"
    default_message = "Verification code is incorrect"

    def __init__(self, message: str | None = None, *, attempts_remaining: int = 0) -> None:
        super().__init__(message)
        self.attempts_remaining = attempts_remaining


class EmailChangeVerificationFailedLastError(AuthDomainError):
    """All verification attempts exhausted — request deleted."""

    status_code = 401
    code = "VERIFICATION_FAILED_LAST"
    default_message = "Verification code is incorrect — all attempts exhausted"


# ---------------------------------------------------------------------------
# Saved-filter errors (v1.23.0 — registry-filters feature)
# ---------------------------------------------------------------------------


class SavedFilterNotFoundError(AuthDomainError):
    """Requested preset does not exist for the given user."""

    status_code = 404
    default_message = "Saved filter not found"


class SavedFilterLimitExceededError(AuthDomainError):
    """User has reached the maximum number of allowed filter presets (20)."""

    status_code = 409
    code = "FILTER_LIMIT_EXCEEDED"
    default_message = "Достигнут лимит пресетов (20). Удалите один из существующих."


class SavedFilterNameTakenError(AuthDomainError):
    """A preset with this name already exists for this user."""

    status_code = 409
    code = "FILTER_NAME_TAKEN"
    default_message = "Пресет с таким именем уже существует"


class SavedFilterJsonInvalidError(AuthDomainError):
    """filter_json is not a JSON object."""

    status_code = 422
    code = "FILTER_JSON_INVALID"
    default_message = "filter_json must be a JSON object"
