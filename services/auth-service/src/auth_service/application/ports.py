# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Repository and service port protocols for auth-service.

Use cases depend on these Protocols ONLY — never on SQLAlchemy or Redis directly.
Infrastructure adapters implement these protocols.

Per Iron Rule 2: "Repositories are Protocols in application/ports.py."
"""

from __future__ import annotations

import uuid
from typing import Protocol

from lotsman_shared.envelope import EventEnvelope

from auth_service.domain.entities import (
    BackupCode,
    LoginAttempt,
    SavedFilter,
    Session,
    TotpUsedCode,
    User,
)
from auth_service.domain.value_objects import TicketScope

# ---------------------------------------------------------------------------
# Repository protocols
# ---------------------------------------------------------------------------


class UserRepository(Protocol):
    """Port for user persistence."""

    async def get_by_id(self, user_id: uuid.UUID) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None: ...

    async def add(self, user: User) -> None: ...

    async def update(self, user: User) -> None: ...

    async def count_active_admins(self) -> int: ...

    async def count_active_by_role(self, role: str) -> int: ...

    async def list_all(self) -> list[User]: ...


class SessionRepository(Protocol):
    """Port for session persistence."""

    async def get_by_id(self, session_id: uuid.UUID) -> Session | None: ...

    async def get_by_refresh_hash(self, refresh_hash: str) -> Session | None: ...

    async def list_active_for_user(self, user_id: uuid.UUID) -> list[Session]: ...

    async def add(self, session: Session) -> None: ...

    async def revoke(self, session_id: uuid.UUID) -> None: ...

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int: ...

    async def revoke_all_except(self, user_id: uuid.UUID, except_session_id: uuid.UUID) -> int: ...


class LoginAttemptRepository(Protocol):
    """Port for login-attempt recording and lockout queries."""

    async def add(self, attempt: LoginAttempt) -> None: ...

    async def count_failures_since(self, email: str, since_seconds: int) -> int: ...

    async def has_success_after_last_failure(self, email: str, window_seconds: int) -> bool: ...


class BackupCodeRepository(Protocol):
    """Port for TOTP backup code storage."""

    async def list_unused_for_user(self, user_id: uuid.UUID) -> list[BackupCode]: ...

    async def count_unused_for_user(self, user_id: uuid.UUID) -> int: ...

    async def add_batch(self, codes: list[BackupCode]) -> None: ...

    async def delete_all_for_user(self, user_id: uuid.UUID) -> None: ...

    async def mark_used(self, code_id: uuid.UUID) -> None: ...


class TotpUsedCodeRepository(Protocol):
    """Port for TOTP anti-replay period_index tracking."""

    async def exists(self, user_id: uuid.UUID, period_index: int) -> bool: ...

    async def add(self, record: TotpUsedCode) -> None: ...


class SavedFilterRepository(Protocol):
    """Port for persisting user-owned named filter presets."""

    async def list_for_user(self, user_id: uuid.UUID) -> list[SavedFilter]: ...

    async def get_by_id(self, filter_id: uuid.UUID, user_id: uuid.UUID) -> SavedFilter | None:
        """Return the preset if it belongs to user_id, else None."""
        ...

    async def name_exists(self, user_id: uuid.UUID, name: str) -> bool:
        """True if user already has a preset with this name."""
        ...

    async def count_for_user(self, user_id: uuid.UUID) -> int: ...

    async def add(self, saved_filter: SavedFilter) -> None: ...

    async def update(self, saved_filter: SavedFilter) -> None: ...

    async def delete(self, filter_id: uuid.UUID) -> None: ...

    async def unset_default_for_user(self, user_id: uuid.UUID) -> None:
        """Set is_default=False on all presets for user_id (used before setting a new default)."""
        ...


# ---------------------------------------------------------------------------
# Crypto / service ports
# ---------------------------------------------------------------------------


class PasswordHasher(Protocol):
    """Port for argon2id password hashing."""

    def hash(self, password: str) -> str: ...

    def verify(self, hash: str, password: str) -> bool: ...

    def check_needs_rehash(self, hash: str) -> bool: ...


class TotpService(Protocol):
    """Port for TOTP secret generation and verification."""

    def generate_secret_b32(self) -> str: ...

    def make_otpauth_url(self, *, email: str, secret_b32: str, issuer: str) -> str: ...

    def verify(self, secret_b32: str, code: str, *, valid_window: int = 1) -> bool: ...

    def current_period_index(self) -> int: ...


class EncryptionService(Protocol):
    """Port for symmetric encryption of TOTP secrets (Fernet)."""

    def encrypt(self, plaintext: str) -> bytes: ...

    def decrypt(self, ciphertext: bytes) -> str: ...


class JwtIssuer(Protocol):
    """Port for issuing RS256 access JWTs."""

    def issue(
        self,
        *,
        user_id: uuid.UUID,
        email: str,
        role: str,
        session_id: uuid.UUID,
    ) -> str: ...


class EventOutbox(Protocol):
    """Port for writing events to the transactional outbox.

    Implementations MUST write in the SAME database session/transaction as the
    business mutation to guarantee atomicity (Iron Rule 6).
    """

    async def publish(self, envelope: EventEnvelope) -> None: ...


# ---------------------------------------------------------------------------
# Redis port protocols
# ---------------------------------------------------------------------------


class RedisLockoutStore(Protocol):
    """Admin instant-lockout Redis SET (no TTL — permanent until admin removes)."""

    async def set_locked(self, user_id: uuid.UUID) -> None: ...

    async def is_locked(self, user_id: uuid.UUID) -> bool: ...

    async def remove_locked(self, user_id: uuid.UUID) -> None: ...


class RedisTotpEnrollmentStore(Protocol):
    """Pending TOTP enrollment (5-minute TTL)."""

    async def set_pending(self, user_id: uuid.UUID, secret_b32: str) -> None: ...

    async def get_pending(self, user_id: uuid.UUID) -> str | None: ...

    async def delete_pending(self, user_id: uuid.UUID) -> None: ...


class RedisReplayCache(Protocol):
    """JTI replay cache for internal JWTs (ADR-0003 §10 R-5c / F-003)."""

    async def check_and_set(self, audience: str, jti: str, ttl_seconds: int) -> bool:
        """Return True if this jti is first-sight (and cache it). False on replay."""
        ...


class RedisAdminOtpStore(Protocol):
    """Admin-issued OOB OTP store (10-minute TTL, single-use)."""

    async def set_otp(self, user_id: uuid.UUID, otp_hash: str) -> None: ...

    async def get_otp_hash(self, user_id: uuid.UUID) -> str | None: ...

    async def delete_otp(self, user_id: uuid.UUID) -> None: ...


class RedisPendingTotpLoginStore(Protocol):
    """Pending TOTP login ticket after password is verified (5-minute TTL).

    ADR-0008 rev. 3 D1.2 / MF-1: the Redis value is a JSON object carrying a
    scope discriminator (``{"uid": "<uuid>", "scope": "enroll"|"login"}``).
    Both ``set_ticket`` and ``get_user_id`` require an explicit ``TicketScope``
    so callers cannot accidentally omit it.
    """

    async def set_ticket(
        self,
        ticket_id: str,
        user_id: uuid.UUID,
        scope: TicketScope,  # required — no default
    ) -> None: ...

    async def get_user_id(
        self,
        ticket_id: str,
        *,
        expected_scope: TicketScope,  # keyword-only, required — no default
    ) -> uuid.UUID | None:
        """Return the user UUID if the ticket exists and its scope matches.

        Returns None for: missing / expired / malformed JSON / bare-string
        legacy values / scope mismatch.  Never raises.
        """
        ...

    async def delete_ticket(self, ticket_id: str) -> None: ...

    async def increment_confirm_attempts(self, ticket_id: str) -> int:
        """INCR the per-ticket failed-confirm counter (same 300 s TTL as the ticket).

        Returns the new counter value.  Caller compares against MAX_CONFIRM_ATTEMPTS (5).
        Key: totp:login:pending:attempts:<ticket_id>.
        """
        ...

    async def delete_confirm_attempts(self, ticket_id: str) -> None:
        """Delete the per-ticket confirm-attempt counter (called on ticket consumption)."""
        ...


class RedisReMfaStore(Protocol):
    """Tracks recent TOTP re-MFA completions (5-minute TTL)."""

    async def set_verified(self, user_id: uuid.UUID, session_id: uuid.UUID) -> None: ...

    async def is_verified(self, user_id: uuid.UUID, session_id: uuid.UUID) -> bool: ...


class RedisBootstrapOtpStore(Protocol):
    """Bootstrap OTP store keyed by email (ADR-0004 §3).

    Key prefix: ``bootstrap:otp:<email>``
    TTL: 24 hours (86400 seconds).
    Distinct from RedisAdminOtpStore (which is keyed by user_id, 10-min TTL).
    """

    async def set_otp(self, email: str, otp_hash: str) -> None: ...

    async def get_otp_hash(self, email: str) -> str | None: ...

    async def delete_otp(self, email: str) -> None: ...


# ---------------------------------------------------------------------------
# Utility ports
# ---------------------------------------------------------------------------


class BreachedPasswordChecker(Protocol):
    """Port for HIBP breached-password screening (local bundle, no egress)."""

    def is_breached(self, password: str) -> bool: ...


class Clock(Protocol):
    """Provides the current UTC time. Tests inject FakeClock."""

    def now(self) -> datetime:  # noqa: F821
        ...


# Fix the datetime import for Clock return type
from datetime import datetime  # noqa: E402

# ---------------------------------------------------------------------------
# Channel directory port (ADR-0004 Phase 2b — invite_user use case)
# ---------------------------------------------------------------------------


class ChannelDirectoryReader(Protocol):
    """Port to query enabled notification channels.

    Implemented by ChannelDirectoryHttpAdapter in infrastructure/,
    which calls notification-service GET /api/v1/admin/channels.
    """

    async def get_enabled_channels(self) -> list[str]:
        """Return list of enabled channel names in priority order."""
        ...


class RedisEmailChangeStore(Protocol):
    """Pending email-change request store (15-minute TTL).

    Key prefix: ``email_change:<request_id>``
    TTL: 900 seconds (15 min).
    """

    async def set_request(
        self,
        request_id: str,
        *,
        user_id: uuid.UUID,
        new_email: str,
        code_hash: str,
        attempts_remaining: int,
    ) -> None: ...

    async def get_request(self, request_id: str) -> dict[str, object] | None:
        """Return the stored request dict or None if expired / not found."""
        ...

    async def delete_request(self, request_id: str) -> None: ...

    async def decrement_attempts(self, request_id: str) -> int:
        """Decrement attempts_remaining by 1, persist, and return the new value.

        Caller is responsible for deleting the key if the returned value is 0.
        """
        ...


class TransactionalEmailSender(Protocol):
    """Port for sending a single transactional email.

    Implemented by NotificationServiceEmailAdapter in infrastructure/.
    The use case depends on this Protocol — it never calls notification-service
    or aiosmtplib directly.
    """

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body_text: str,
    ) -> None:
        """Send an email.

        Raises:
            EmailChannelNotConfiguredError: if the email channel is not enabled/configured.
            Exception: on SMTP / provider error (caller converts to 502).
        """
        ...


class InviteOtpPublisher(Protocol):
    """Side-channel OTP delivery port (F-001 / ADR-0004 §6).

    Writes the plaintext OTP to a one-shot Redis key
    ``invite:otp:{invitation_id}`` with a short TTL so the OTP never
    enters the persistent outbox table or Redis Streams.

    The notification-service consumer reads the key exactly once and
    calls DEL immediately after reading.
    """

    async def publish(
        self,
        invitation_id: uuid.UUID,
        otp: str,
        ttl_seconds: int = 600,
    ) -> None:
        """Store *otp* under ``invite:otp:{invitation_id}`` with TTL seconds."""
        ...
