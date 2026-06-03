# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Initial auth schema — users, sessions, login_attempts, outbox, outbox_dlq.

Revision ID: 0001
Revises: (none — base)
Create Date: 2026-05-06

Design decisions:
- email column uses citext (case-insensitive) — autogenerate will show it as
  String; that divergence is intentional and documented here.
- ip_address columns use inet PG type (not VARCHAR) for proper IP comparison.
- Soft-delete via deleted_at on auth.users; partial unique index enforces
  email uniqueness among non-deleted rows only.
- updated_at on users is maintained by a BEFORE UPDATE trigger.
- Outbox partial index on dispatched_at IS NULL covers the hot dispatcher query.
- Role grants are emitted here to keep them versioned alongside the schema.
"""

from __future__ import annotations

from alembic import op

# revision identifiers
revision: str = "0001_initial_auth_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Schema (idempotent — env.py also issues CREATE SCHEMA IF NOT EXISTS
    # but explicit here for clarity in SQL audit trail)
    # ------------------------------------------------------------------
    op.execute("CREATE SCHEMA IF NOT EXISTS auth")

    # ------------------------------------------------------------------
    # updated_at trigger function (shared across all tables in this schema)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION auth.set_updated_at()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$
    """)

    # ------------------------------------------------------------------
    # auth.users
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS auth.users (
            id              UUID        NOT NULL DEFAULT gen_random_uuid(),
            email           CITEXT      NOT NULL,
            full_name       TEXT        NOT NULL,
            password_hash   TEXT        NOT NULL,
            totp_secret_enc BYTEA       NOT NULL,
            role            TEXT        NOT NULL,
            is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
            last_login_at   TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at      TIMESTAMPTZ,
            CONSTRAINT users_pk             PRIMARY KEY (id),
            CONSTRAINT users_role_check     CHECK (role IN ('admin', 'editor', 'viewer'))
        )
    """)
    op.execute(
        "COMMENT ON TABLE auth.users IS "
        "'Human users of the Lotsman application. Soft-deleted via deleted_at.'"
    )
    op.execute(
        "COMMENT ON COLUMN auth.users.password_hash IS "
        "'argon2id hash. SYSTEM sentinel value for system actors.'"
    )
    op.execute(
        "COMMENT ON COLUMN auth.users.totp_secret_enc IS "
        "'AES-GCM encrypted TOTP secret. Zero byte for system actors.'"
    )

    # Partial unique: email uniqueness only among non-deleted users.
    # A plain UNIQUE on email would block re-registration after soft-delete.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS users_email_active_uidx
            ON auth.users (email)
            WHERE deleted_at IS NULL
    """)
    op.execute(
        "COMMENT ON INDEX auth.users_email_active_uidx IS "
        "'Enforces unique email among non-deleted (active + system) users.'"
    )

    # updated_at trigger
    op.execute("""
        CREATE TRIGGER users_set_updated_at
            BEFORE UPDATE ON auth.users
            FOR EACH ROW EXECUTE FUNCTION auth.set_updated_at()
    """)

    # ------------------------------------------------------------------
    # auth.sessions
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS auth.sessions (
            id           UUID        NOT NULL DEFAULT gen_random_uuid(),
            user_id      UUID        NOT NULL,
            refresh_hash TEXT        NOT NULL,
            user_agent   TEXT,
            ip_address   INET,
            expires_at   TIMESTAMPTZ NOT NULL,
            revoked_at   TIMESTAMPTZ,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT sessions_pk      PRIMARY KEY (id),
            CONSTRAINT sessions_user_fk FOREIGN KEY (user_id)
                REFERENCES auth.users (id)
                ON DELETE CASCADE
                DEFERRABLE INITIALLY DEFERRED
        )
    """)
    op.execute(
        "COMMENT ON TABLE auth.sessions IS "
        "'Active and revoked user sessions (refresh-token records).'"
    )
    op.execute(
        "COMMENT ON COLUMN auth.sessions.refresh_hash IS "
        "'SHA-256 hex digest of the opaque refresh token.'"
    )

    # Hot query: "find active sessions for a user" (used by session list endpoint)
    op.execute("""
        CREATE INDEX IF NOT EXISTS sessions_user_active_idx
            ON auth.sessions (user_id)
            WHERE revoked_at IS NULL
    """)
    op.execute(
        "COMMENT ON INDEX auth.sessions_user_active_idx IS "
        "'Active sessions per user — used by session-list and revoke-all endpoints.'"
    )

    # Hot query: "delete expired sessions" (cleanup job uses expires_at)
    op.execute("""
        CREATE INDEX IF NOT EXISTS sessions_expires_at_idx
            ON auth.sessions (expires_at)
            WHERE revoked_at IS NULL
    """)
    op.execute(
        "COMMENT ON INDEX auth.sessions_expires_at_idx IS "
        "'Expired-session cleanup job: SELECT ... WHERE expires_at < now() AND revoked_at IS NULL.'"
    )

    # ------------------------------------------------------------------
    # auth.login_attempts
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS auth.login_attempts (
            id         UUID        NOT NULL DEFAULT gen_random_uuid(),
            email      CITEXT      NOT NULL,
            ip_address INET,
            outcome    TEXT        NOT NULL,
            user_agent TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT login_attempts_pk             PRIMARY KEY (id),
            CONSTRAINT login_attempts_outcome_check  CHECK (
                outcome IN ('success', 'failed_password', 'failed_totp', 'locked')
            )
        )
    """)
    op.execute(
        "COMMENT ON TABLE auth.login_attempts IS "
        "'Per-email login attempt log. Read by security lockout logic.'"
    )

    # Hot query: "count failed attempts for this email in the last N minutes"
    op.execute("""
        CREATE INDEX IF NOT EXISTS login_attempts_email_created_idx
            ON auth.login_attempts (email, created_at DESC)
            WHERE outcome != 'success'
    """)
    op.execute(
        "COMMENT ON INDEX auth.login_attempts_email_created_idx IS "
        "'Rate-limit check: recent failed attempts per email. Covers the lockout window query.'"
    )

    # ------------------------------------------------------------------
    # auth.outbox
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS auth.outbox (
            id            UUID        NOT NULL DEFAULT gen_random_uuid(),
            occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            dispatched_at TIMESTAMPTZ,
            topic         TEXT        NOT NULL,
            payload       JSONB       NOT NULL,
            CONSTRAINT outbox_pk PRIMARY KEY (id)
        )
    """)
    op.execute(
        "COMMENT ON TABLE auth.outbox IS "
        "'Transactional outbox for auth domain events (auth.users, auth.sessions streams).'"
    )
    op.execute(
        "COMMENT ON COLUMN auth.outbox.topic IS 'Redis Stream key: auth.users | auth.sessions'"
    )

    # Dispatcher hot query: undispatched rows
    op.execute("""
        CREATE INDEX IF NOT EXISTS outbox_undispatched_idx
            ON auth.outbox (occurred_at)
            WHERE dispatched_at IS NULL
    """)
    op.execute(
        "COMMENT ON INDEX auth.outbox_undispatched_idx IS "
        "'outbox-dispatcher polling query: SELECT ... FOR UPDATE SKIP LOCKED WHERE dispatched_at IS NULL.'"
    )

    # ------------------------------------------------------------------
    # auth.outbox_dlq
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS auth.outbox_dlq (
            id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            occurred_at TIMESTAMPTZ NOT NULL,
            topic       TEXT        NOT NULL,
            payload     JSONB       NOT NULL,
            failed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_error  TEXT        NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT outbox_dlq_pk PRIMARY KEY (id)
        )
    """)
    op.execute(
        "COMMENT ON TABLE auth.outbox_dlq IS "
        "'Dead-letter queue for auth.outbox rows that exhausted all dispatch retries.'"
    )

    # ------------------------------------------------------------------
    # Table-level grants for auth_app role
    # (default privileges in 01-schemas-and-roles.sql cover future tables,
    # but we must also grant on these tables created by the superuser/migrator)
    # ------------------------------------------------------------------
    for table in ("users", "sessions", "login_attempts", "outbox", "outbox_dlq"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON auth.{table} TO auth_app")


def downgrade() -> None:
    # Dropping the schema CASCADE is safe for the initial migration only.
    # Every subsequent migration must write a precise downgrade.
    op.execute("DROP SCHEMA IF EXISTS auth CASCADE")
