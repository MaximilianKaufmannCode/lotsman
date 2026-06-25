# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Domain entities for auth-service.

Pure Python — no SQLAlchemy, no FastAPI imports here.
Only stdlib + pydantic allowed (per Iron Rule 1: domain has no outward deps).
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYSTEM_PASSWORD_SENTINEL = "SYSTEM"
TOTP_SENTINEL = b"\x00"

# Per-user web-interface font-size preference, stored as a percent of the base
# (100 == default / current look). Bounds mirror the DB CHECK and the API clamp.
UI_FONT_SCALE_DEFAULT = 100
UI_FONT_SCALE_MIN = 80
UI_FONT_SCALE_MAX = 150


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


@dataclass
class User:
    """Represents an authenticated human user of Лоцман.

    system actors have password_hash == SYSTEM_PASSWORD_SENTINEL and
    totp_secret_enc == TOTP_SENTINEL and is_active == False.
    """

    id: uuid.UUID
    email: str
    full_name: str
    password_hash: str
    totp_secret_enc: bytes
    role: str  # "admin" | "editor" | "viewer"
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    # Web-interface font-size preference (percent of base; 100 = default).
    # Defaulted so every existing constructor / test stays backward-compatible.
    ui_font_scale: int = UI_FONT_SCALE_DEFAULT

    @property
    def is_system_actor(self) -> bool:
        return self.password_hash == SYSTEM_PASSWORD_SENTINEL

    @property
    def has_totp_enrolled(self) -> bool:
        return self.totp_secret_enc != TOTP_SENTINEL

    @classmethod
    def create_new(
        cls,
        *,
        email: str,
        full_name: str,
        password_hash: str,
        role: str,
        now: datetime | None = None,
    ) -> User:
        """Factory for a new uninvited user (no TOTP enrolled yet)."""
        ts = now or datetime.now(tz=UTC)
        return cls(
            id=uuid.uuid4(),
            email=email.strip().lower(),
            full_name=full_name,
            password_hash=password_hash,
            totp_secret_enc=TOTP_SENTINEL,
            role=role,
            is_active=True,
            must_change_password=True,
            last_login_at=None,
            created_at=ts,
            updated_at=ts,
            deleted_at=None,
        )


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@dataclass
class Session:
    """An active user login session linked to a refresh token.

    The plaintext refresh token is NEVER stored here; only its SHA-256 hex digest.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    refresh_hash: str  # sha256(opaque refresh token) in hex
    user_agent: str | None
    ip_address: str | None
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime

    @property
    def is_active(self) -> bool:
        now = datetime.now(tz=UTC)
        return self.revoked_at is None and self.expires_at > now

    @classmethod
    def create(
        cls,
        *,
        user_id: uuid.UUID,
        refresh_hash: str,
        user_agent: str | None,
        ip_address: str | None,
        ttl_seconds: int = 43200,
        now: datetime | None = None,
    ) -> Session:
        """Create a new session with a configurable TTL in seconds.

        Default 43200 s (12 h) per user request 2026-05-12.
        Previously 7 days; callers should pass settings.refresh_token_ttl_seconds.
        """
        ts = now or datetime.now(tz=UTC)
        from datetime import timedelta

        return cls(
            id=uuid.uuid4(),
            user_id=user_id,
            refresh_hash=refresh_hash,
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=ts + timedelta(seconds=ttl_seconds),
            revoked_at=None,
            created_at=ts,
        )


# ---------------------------------------------------------------------------
# LoginAttempt
# ---------------------------------------------------------------------------


@dataclass
class LoginAttempt:
    """Records a single login attempt for lockout calculation."""

    id: uuid.UUID
    email: str
    outcome: str  # LoginOutcome value
    ip_address: str | None
    user_agent: str | None
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        email: str,
        outcome: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        now: datetime | None = None,
    ) -> LoginAttempt:
        return cls(
            id=uuid.uuid4(),
            email=email.strip().lower(),
            outcome=outcome,
            ip_address=ip_address,
            user_agent=user_agent,
            created_at=now or datetime.now(tz=UTC),
        )


# ---------------------------------------------------------------------------
# BackupCode
# ---------------------------------------------------------------------------


@dataclass
class BackupCode:
    """A single argon2id-hashed TOTP backup code for a user."""

    id: uuid.UUID
    user_id: uuid.UUID
    code_hash: str  # argon2id hash of the plaintext backup code
    used_at: datetime | None
    created_at: datetime

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    @classmethod
    def create(
        cls,
        *,
        user_id: uuid.UUID,
        code_hash: str,
        now: datetime | None = None,
    ) -> BackupCode:
        ts = now or datetime.now(tz=UTC)
        return cls(
            id=uuid.uuid4(),
            user_id=user_id,
            code_hash=code_hash,
            used_at=None,
            created_at=ts,
        )


# ---------------------------------------------------------------------------
# TotpUsedCode  (anti-replay)
# ---------------------------------------------------------------------------


@dataclass
class TotpUsedCode:
    """Records a used TOTP period_index per user to prevent replay attacks.

    Uniqueness constraint: (user_id, period_index).
    """

    id: uuid.UUID
    user_id: uuid.UUID
    period_index: int  # floor(unix_time / 30)
    used_at: datetime

    @classmethod
    def create(
        cls,
        *,
        user_id: uuid.UUID,
        period_index: int,
        now: datetime | None = None,
    ) -> TotpUsedCode:
        return cls(
            id=uuid.uuid4(),
            user_id=user_id,
            period_index=period_index,
            used_at=now or datetime.now(tz=UTC),
        )


# ---------------------------------------------------------------------------
# OobOtp (Redis-only, not a persisted entity)
# ---------------------------------------------------------------------------


@dataclass
class OobOtpTicket:
    """Transient: admin-issued out-of-band OTP for first-login or password reset.

    Stored in Redis with 10-minute TTL, keyed by user_id.
    The plaintext OTP is revealed ONCE to the admin and then only stored as
    argon2id hash in auth.users.password_hash.
    """

    user_id: uuid.UUID
    plaintext_otp: str  # shown once to admin; never persisted after hashing
    created_at: datetime

    @classmethod
    def generate(cls, *, user_id: uuid.UUID, now: datetime | None = None) -> OobOtpTicket:
        """Generate a cryptographically random 12-character OTP."""
        # 12 chars of URL-safe base64 → ~72 bits of entropy
        otp = secrets.token_urlsafe(9)  # 9 bytes → 12 base64 chars
        return cls(
            user_id=user_id,
            plaintext_otp=otp,
            created_at=now or datetime.now(tz=UTC),
        )


# ---------------------------------------------------------------------------
# TotpEnrollmentTicket (Redis-only pending state)
# ---------------------------------------------------------------------------


@dataclass
class TotpEnrollmentTicket:
    """Transient: pending TOTP enrollment stored in Redis for 5 minutes.

    The secret_b32 is stored in Redis (not in the DB column) until the user
    confirms the code. On confirmation, the encrypted secret is written to
    auth.users.totp_secret_enc.
    """

    user_id: uuid.UUID
    secret_b32: str
    created_at: datetime


# ---------------------------------------------------------------------------
# PendingTotpLoginTicket (Redis-only)
# ---------------------------------------------------------------------------


@dataclass
class PendingTotpLoginTicket:
    """Transient: after password verification succeeds, stored in Redis pending TOTP.

    Keyed by an opaque session token returned to the client, mapping to user_id.
    TTL: 5 minutes.
    """

    ticket_id: str  # opaque random value returned to client
    user_id: uuid.UUID
    created_at: datetime

    @classmethod
    def generate(cls, *, user_id: uuid.UUID, now: datetime | None = None) -> PendingTotpLoginTicket:
        return cls(
            ticket_id=secrets.token_urlsafe(32),
            user_id=user_id,
            created_at=now or datetime.now(tz=UTC),
        )


# ---------------------------------------------------------------------------
# SavedFilter  — per-user named filter preset (v1.23.0)
# ---------------------------------------------------------------------------


@dataclass
class SavedFilter:
    """A named filter preset saved by a user for the document registry grid.

    filter_json is an arbitrary JSON object owned by the frontend; the domain
    enforces only that it is a dict (not None, not a list, not a scalar).
    Maximum 20 presets per user (enforced at application layer).
    """

    id: uuid.UUID
    user_id: uuid.UUID
    name: str  # 1–100 characters, unique per user
    filter_json: dict  # type: ignore[type-arg]
    is_default: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        user_id: uuid.UUID,
        name: str,
        filter_json: dict,  # type: ignore[type-arg]
        is_default: bool = False,
        now: datetime | None = None,
    ) -> SavedFilter:
        ts = now or datetime.now(tz=UTC)
        return cls(
            id=uuid.uuid4(),
            user_id=user_id,
            name=name,
            filter_json=filter_json,
            is_default=is_default,
            created_at=ts,
            updated_at=ts,
        )
