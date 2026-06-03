# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Add auth.totp_used_codes and users.must_change_password.

auth.totp_used_codes stores (user_id, period_index) pairs that have already
been consumed in a TOTP verification. The natural PRIMARY KEY on
(user_id, period_index) is the anti-replay uniqueness constraint: an INSERT
of a duplicate (user_id, period_index) will raise a UniqueViolation that the
application converts to TotpInvalidError — so no separate "did this period
fire already?" SELECT is needed.

users.must_change_password is added here because the column was modelled in
models.py from day-one (ADR-0003 §5b admin password-reset flow) but was
omitted from migration 0001. The default FALSE preserves all existing rows.

Revision ID: 0003_add_totp_used_codes
Revises: 0002_seed_system_actors
Create Date: 2026-05-06
"""

from __future__ import annotations

from alembic import op

revision: str = "0003_add_totp_used_codes"
down_revision = "0002_seed_system_actors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # auth.users — add must_change_password (missing from 0001)
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE auth.users
            ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE
    """)
    op.execute(
        "COMMENT ON COLUMN auth.users.must_change_password IS "
        "'Set to TRUE when admin issues an OOB OTP (first login or admin password reset). "
        "Forces the user to POST /api/v1/auth/password/change before any other endpoint.'"
    )

    # ------------------------------------------------------------------
    # auth.totp_used_codes — TOTP anti-replay table
    #
    # Uniqueness is the PRIMARY KEY (user_id, period_index):
    #   - period_index = floor(unix_time / 30) for the validated window
    #   - INSERTing a duplicate raises UniqueViolation → TotpInvalidError
    #   - No extra index needed; PK covers both the INSERT check and any
    #     EXISTS lookup (verify_totp.py calls totp_used_repo.exists()).
    #
    # Real FK with ON DELETE CASCADE: when auth.users is hard-deleted
    # (currently only in tests; production uses soft-delete), codes are
    # removed automatically.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS auth.totp_used_codes (
            user_id      UUID        NOT NULL,
            period_index BIGINT      NOT NULL,
            used_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT totp_used_codes_pk
                PRIMARY KEY (user_id, period_index),
            CONSTRAINT totp_used_codes_user_fk
                FOREIGN KEY (user_id)
                REFERENCES auth.users (id)
                ON DELETE CASCADE
                DEFERRABLE INITIALLY DEFERRED
        )
    """)
    op.execute(
        "COMMENT ON TABLE auth.totp_used_codes IS "
        "'Anti-replay: records every (user_id, period_index) that has been accepted in "
        "a TOTP verification. period_index = floor(unix_epoch / 30). Inserting a duplicate "
        "violates the PRIMARY KEY and triggers a replay rejection. Rows older than the "
        "maximum valid_window (±1 step = 90 seconds) may be pruned by a cleanup job.'"
    )
    op.execute(
        "COMMENT ON COLUMN auth.totp_used_codes.period_index IS "
        "'floor(unix_time / 30) at the moment the TOTP code was verified. "
        "Uniqueness per user prevents code reuse within the acceptance window.'"
    )

    op.execute("GRANT SELECT, INSERT, DELETE ON auth.totp_used_codes TO auth_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth.totp_used_codes")
    op.execute("ALTER TABLE auth.users DROP COLUMN IF EXISTS must_change_password")
