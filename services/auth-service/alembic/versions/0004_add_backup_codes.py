# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Add auth.backup_codes table.

Stores argon2id-hashed single-use backup codes per user (ADR-0003 §5).
Each user gets exactly 10 codes at TOTP enrollment; codes are invalidated by
setting used_at. Regeneration (RegenerateBackupCodes use case) deletes all
rows for the user and inserts 10 new ones in the same transaction.

Verification path (verify_totp.py): list all unused codes for the user via
the partial index, then argon2id.verify each candidate — O(N) with N ≤ 10.
This is deliberately iterative; argon2id makes a hash-index lookup useless.

Revision ID: 0004_add_backup_codes
Revises: 0003_add_totp_used_codes
Create Date: 2026-05-06
"""

from __future__ import annotations

from alembic import op

revision: str = "0004_add_backup_codes"
down_revision = "0003_add_totp_used_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # auth.backup_codes
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS auth.backup_codes (
            id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            user_id     UUID        NOT NULL,
            code_hash   TEXT        NOT NULL,
            used_at     TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT backup_codes_pk      PRIMARY KEY (id),
            CONSTRAINT backup_codes_user_fk FOREIGN KEY (user_id)
                REFERENCES auth.users (id)
                ON DELETE CASCADE
                DEFERRABLE INITIALLY DEFERRED
        )
    """)
    op.execute(
        "COMMENT ON TABLE auth.backup_codes IS "
        "'Single-use TOTP recovery codes (argon2id hashed). Exactly 10 per user at "
        "enrollment. used_at IS NULL means the code is still valid. Regeneration "
        "deletes all prior rows and inserts 10 new ones in the same transaction.'"
    )
    op.execute(
        "COMMENT ON COLUMN auth.backup_codes.code_hash IS "
        "'argon2id PHC string of the 4-4 hex plaintext code (e.g. A1B2-C3D4). "
        "The plaintext is displayed once and never stored.'"
    )
    op.execute(
        "COMMENT ON COLUMN auth.backup_codes.used_at IS "
        "'NULL = unused (valid). Set to now() when the code is consumed at login. "
        "Consumed codes are never deleted so the audit trail remains intact.'"
    )

    # ------------------------------------------------------------------
    # Indexes
    #
    # idx_backup_codes_user_unused:
    #   Partial index on (user_id) WHERE used_at IS NULL.
    #   Serves three hot queries:
    #     1. list_unused_for_user — fetches ≤10 rows for the verify loop
    #     2. count_unused_for_user — counts remaining codes (low-stock warning)
    #     3. delete_all_for_user (regeneration) touches full table, not this idx
    #
    # No separate (user_id, code_hash) index is needed: argon2id hashes cannot
    # be looked up by value; verification always iterates the unused set.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_backup_codes_user_unused
            ON auth.backup_codes (user_id)
            WHERE used_at IS NULL
    """)
    op.execute(
        "COMMENT ON INDEX auth.idx_backup_codes_user_unused IS "
        "'Hot path: list ≤10 unused backup codes per user for argon2id verification. "
        "Also used for the low-stock count (≤2 remaining triggers a warning in the API).'"
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON auth.backup_codes TO auth_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth.backup_codes")
