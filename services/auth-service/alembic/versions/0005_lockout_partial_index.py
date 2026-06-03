# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Add targeted partial index for login-attempt lockout query.

Migration 0001 created login_attempts_email_created_idx with
  WHERE outcome != 'success'
which covers the hot lockout-check query but is broader than needed. This
migration adds a named partial index that covers exactly the three failure
outcomes the check_lockout use case counts.

The existing general index is kept: it remains useful for any read that wants
all non-success rows. The new index is narrower and cheaper per lookup.

CREATE INDEX CONCURRENTLY is not available inside a DDL transaction. The index
is created with plain CREATE INDEX here. On a production database with a large
login_attempts table, run the following AFTER applying this migration (it can
run while the application is live):

    DROP INDEX CONCURRENTLY IF EXISTS auth.idx_login_attempts_lockout;
    CREATE INDEX CONCURRENTLY idx_login_attempts_lockout
        ON auth.login_attempts (email, created_at DESC)
        WHERE outcome IN ('failed_password', 'failed_totp', 'locked');

The CHECK constraint on auth.login_attempts.outcome already covers
('success', 'failed_password', 'failed_totp', 'locked'); no new outcome
values are introduced by this migration.

Revision ID: 0005_lockout_partial_index
Revises: 0004_add_backup_codes
Create Date: 2026-05-06
"""

from __future__ import annotations

from alembic import op

revision: str = "0005_lockout_partial_index"
down_revision = "0004_add_backup_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Targeted partial index for the lockout check query:
    #   SELECT count(*) FROM auth.login_attempts
    #   WHERE email = $1
    #     AND created_at > now() - interval '$2 seconds'
    #     AND outcome IN ('failed_password', 'failed_totp', 'locked')
    #
    # Note: CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
    # Alembic runs in a transaction by default, so we use plain CREATE INDEX.
    # The above CONCURRENTLY procedure should be followed in production.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_login_attempts_lockout
            ON auth.login_attempts (email, created_at DESC)
            WHERE outcome IN ('failed_password', 'failed_totp', 'locked')
    """)
    op.execute(
        "COMMENT ON INDEX auth.idx_login_attempts_lockout IS "
        "'Partial index for the lockout-check query: count recent failures per email. "
        "Covers outcomes: failed_password, failed_totp, locked. "
        "Narrower than login_attempts_email_created_idx (outcome != success) — "
        "use CONCURRENTLY to recreate on large tables without lock (see migration header).'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS auth.idx_login_attempts_lockout")
