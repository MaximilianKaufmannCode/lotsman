# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""SQLAlchemy 2.x ORM models for the auth schema.

All tables use:
  - UUIDv4 PKs (gen_random_uuid()) — UUIDv7 when PG17 ships
  - TIMESTAMPTZ for all timestamps
  - soft-delete via deleted_at on auth.users
  - updated_at maintained by DB trigger (see migration)

This module is the single source of truth for autogenerate.  The migration
file 0001_initial_auth_schema.py adds indexes, triggers, and table grants
that SQLAlchemy's autogenerate cannot express.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# TIMESTAMPTZ is spelled TIMESTAMP(timezone=True) in SQLAlchemy 2.x mapped_column usage.
# For raw column() calls, use DateTime(timezone=True). We alias for compat.
TIMESTAMPTZ = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# auth.users
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('super_admin', 'admin', 'editor', 'viewer')", name="users_role_check"
        ),
        CheckConstraint(
            "ui_font_scale BETWEEN 80 AND 150", name="users_ui_font_scale_chk"
        ),
        # Partial unique on email for non-deleted users only.
        # IMPORTANT: the partial index (WHERE deleted_at IS NULL) is created
        # explicitly in the migration; the UniqueConstraint here is intentionally
        # NOT added to avoid autogenerate emitting a full unique constraint.
        {"schema": "auth", "comment": "Human users of the Lotsman application."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(
        # citext is a PG extension type; use String here and let the migration
        # cast via op.execute / raw DDL. Autogenerate will not detect the
        # citext type change — that is intentional; migration owns the DDL.
        String,
        nullable=False,
        comment="Case-insensitive email address (citext in DB). Must be unique among non-deleted users.",
    )
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="argon2id hash of the user password. 'SYSTEM' is a sentinel for system actors.",
    )
    totp_secret_enc: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
        comment="AES-GCM encrypted TOTP secret. Zero byte (\\x00) for system actors.",
    )
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="RBAC role: admin | editor | viewer",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("true"),
        comment="False for deactivated users and system actors.",
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("false"),
        comment="Set to true when admin issues an OOB OTP (first login or password reset).",
    )
    ui_font_scale: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=sa_text("100"),
        comment=(
            "Per-user web-interface font-size preference as a percent of the base "
            "(100 = default/current look). SPA maps percent/100 to the CSS "
            "--app-font-scale multiplier. Bounded 80..150 by users_ui_font_scale_chk."
        ),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
        comment="Touched by trigger on every UPDATE.",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
        comment="Soft-delete timestamp. NULL = active record.",
    )


# ---------------------------------------------------------------------------
# auth.sessions
# ---------------------------------------------------------------------------


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        {"schema": "auth", "comment": "Active and revoked user sessions (refresh-token records)."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    # Logical FK to auth.users — enforced in DB via REFERENCES.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="References auth.users(id). ON DELETE CASCADE.",
    )
    refresh_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="SHA-256 hex digest of the opaque refresh token. Never store the token itself.",
    )
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(
        INET,
        nullable=True,
        comment="Client IP (IPv4 or IPv6). Stored as inet in DB.",
    )
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
    )


# ---------------------------------------------------------------------------
# auth.login_attempts  — rate-limiting and lockout tracking
# ---------------------------------------------------------------------------


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success', 'failed_password', 'failed_totp', 'locked')",
            name="login_attempts_outcome_check",
        ),
        {"schema": "auth", "comment": "Per-email login attempt log for rate-limiting and lockout."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="The email used in the login attempt (citext in DB).",
    )
    ip_address: Mapped[str | None] = mapped_column(
        INET,
        nullable=True,
        comment="Stored as inet in DB.",
    )
    outcome: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="success | failed_password | failed_totp | locked",
    )
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
    )


# ---------------------------------------------------------------------------
# auth.backup_codes  — single-use argon2id-hashed backup codes
# (table name matches migration 0004_add_backup_codes)
# ---------------------------------------------------------------------------


class TotpBackupCode(Base):
    __tablename__ = "backup_codes"
    __table_args__ = (
        {
            "schema": "auth",
            "comment": (
                "Single-use TOTP recovery codes (argon2id hashed). "
                "Exactly 10 per user at enrollment. used_at IS NULL means valid."
            ),
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sa_text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="References auth.users(id). ON DELETE CASCADE.",
    )
    code_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "argon2id PHC string of the 4-4 hex plaintext code (e.g. A1B2-C3D4). "
            "The plaintext is displayed once and never stored."
        ),
    )
    used_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
        comment=(
            "NULL = unused (valid). Set to now() when consumed at login. "
            "Consumed codes are not deleted so the audit trail remains."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )


# ---------------------------------------------------------------------------
# auth.totp_used_codes  — TOTP anti-replay period_index tracking
# (composite PRIMARY KEY matches migration 0003_add_totp_used_codes)
# ---------------------------------------------------------------------------


class TotpUsedCode(Base):
    __tablename__ = "totp_used_codes"
    __table_args__ = (
        {
            "schema": "auth",
            "comment": (
                "Anti-replay: records every (user_id, period_index) accepted in a TOTP "
                "verification. Inserting a duplicate violates the PK and triggers replay "
                "rejection. period_index = floor(unix_epoch / 30)."
            ),
        },
    )

    # Composite primary key — no surrogate UUID column (matches DDL in 0003).
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        nullable=False,
        comment="References auth.users(id). ON DELETE CASCADE.",
    )
    period_index: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        nullable=False,
        comment="floor(unix_time / 30) at verify time. Unique per user_id.",
    )
    used_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )


# ---------------------------------------------------------------------------
# auth.outbox  — transactional outbox (per iron rule #6)
# ---------------------------------------------------------------------------


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        {"schema": "auth", "comment": "Transactional outbox for auth domain events."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
        comment="Set by outbox-dispatcher ARQ worker after XADD to Redis Streams.",
    )
    topic: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Redis Stream key, e.g. 'auth.users' or 'auth.sessions'.",
    )
    payload: Mapped[dict] = mapped_column(  # type: ignore[type-arg]
        JSONB,
        nullable=False,
        comment="Full canonical event envelope (id, type, occurred_at, actor_id, payload, ...).",
    )


# ---------------------------------------------------------------------------
# auth.outbox_dlq  — dead-letter queue for permanently failed outbox rows
# ---------------------------------------------------------------------------


class OutboxDlq(Base):
    __tablename__ = "outbox_dlq"
    __table_args__ = (
        {
            "schema": "auth",
            "comment": "Dead-letter queue for auth.outbox rows that failed all dispatch retries.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]
    failed_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
    )
    last_error: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Last exception message or Redis error from the dispatcher.",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
    )


# ---------------------------------------------------------------------------
# auth.key_rotations  — manual key rotation audit log (migration 0007)
# ---------------------------------------------------------------------------


class KeyRotation(Base):
    __tablename__ = "key_rotations"
    __table_args__ = (
        {
            "schema": "auth",
            "comment": (
                "Manual audit record of cryptographic key rotations. "
                "Populated by super_admin via POST /api/v1/system/keys/{key_id}/rotated. "
                "Seeded at project genesis for all known key_ids."
            ),
        },
    )

    key_id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        comment="Symbolic key identifier, e.g. 'RS256_JWT', 'TOTP_ENC_KEY'.",
    )
    rotated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        comment="When the key was last rotated (supplied by the admin, not server-generated).",
    )
    rotated_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="UUID of the super_admin who recorded the rotation.",
    )
    note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Optional human note about the rotation (reason, ticket number, etc.).",
    )


# ---------------------------------------------------------------------------
# auth.user_saved_filters  — per-user named filter presets (migration 0008)
# ---------------------------------------------------------------------------


class UserSavedFilter(Base):
    __tablename__ = "user_saved_filters"
    __table_args__ = (
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100",
            name="user_saved_filters_name_length_chk",
        ),
        {
            "schema": "auth",
            "comment": (
                "Named filter presets saved by each user for the document registry grid. "
                "filter_json holds the serialised column-filter state. "
                "Added by migration 0008_add_user_saved_filters."
            ),
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="References auth.users(id). ON DELETE CASCADE.",
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Human-readable preset name. Unique per user (enforced by unique index).",
    )
    filter_json: Mapped[dict] = mapped_column(  # type: ignore[type-arg]
        JSONB,
        nullable=False,
        server_default=sa_text("'{}'::jsonb"),
        comment=(
            "Serialised filter state as a JSONB object. "
            "Shape owned by the frontend; recommend a version sentinel key: {\"v\": 1, ...}."
        ),
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("false"),
        comment=(
            "At most one TRUE per user_id, enforced by partial unique index "
            "user_saved_filters_user_default_uidx (WHERE is_default = TRUE)."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
        comment="Touched by trigger on every UPDATE.",
    )
