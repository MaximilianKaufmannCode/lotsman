# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Add notification.provider_credentials — Fernet-encrypted channel configs.

Revision ID: 0002_provider_credentials
Revises: 0001
Create Date: 2026-05-07

What & why
----------
ADR-0004 §4 moves channel credentials (SMTP, Telegram, Dion) out of .env and
into the database so that Space-admin can self-service channel configuration
through the UI without SSH access to the host.

Each channel gets exactly one row (enforced by pc_channel_unique).  The JSON
config blob is Fernet-encrypted (CHANNEL_ENC_KEY env var) before storage, so a
raw DB dump without the key is useless to an attacker.  See US-16 in
the requirements.

Design decisions
----------------
- CREATE TABLE (not ALTER TABLE): the ADR text says "extend existing
  notification.provider_credentials", but 0001 never created that table.
  This migration creates it from scratch — non-destructive on the live dev DB.
- config_enc BYTEA NOT NULL with no column default: every row must hold real
  encrypted bytes.  Empty bytea (E'\\x'::bytea) is reserved for the runtime
  "config wiped" state produced by the application, not a DB default.
- UNIQUE (channel): enforces the "one config row per channel per instance"
  invariant at the DB level, not just in application code.
- The set_updated_at() trigger function already exists from 0001; we reuse it.
- downgrade() drops the table with CASCADE, which also removes the trigger.
"""

from __future__ import annotations

from alembic import op

revision: str = "0002_provider_credentials"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # notification.provider_credentials
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE notification.provider_credentials (
            id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            channel     TEXT        NOT NULL,
            enabled     BOOLEAN     NOT NULL DEFAULT false,
            config_enc  BYTEA       NOT NULL,
            created_by  UUID        NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT provider_credentials_pk     PRIMARY KEY (id),
            CONSTRAINT pc_channel_check            CHECK (channel IN ('email', 'telegram', 'dion')),
            CONSTRAINT pc_channel_unique           UNIQUE (channel)
        )
    """)
    op.execute(
        "COMMENT ON TABLE notification.provider_credentials IS "
        "'One row per notification channel. config_enc is Fernet(CHANNEL_ENC_KEY)-encrypted JSON blob. See ADR-0004 §4.'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.provider_credentials.config_enc IS "
        "'Fernet ciphertext of JSON channel config (smtp_host/port/user/pass, bot_token, etc.). Never stored in plaintext.'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.provider_credentials.created_by IS "
        "'UUID of the admin who last wrote this row, or SYSTEM_MIGRATOR UUID for bootstrap.'"
    )

    # Reuse the trigger function installed in 0001.
    op.execute("""
        CREATE TRIGGER provider_credentials_set_updated_at
            BEFORE UPDATE ON notification.provider_credentials
            FOR EACH ROW EXECUTE FUNCTION notification.set_updated_at()
    """)

    # Grant DML to the per-service role (same pattern as 0001).
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON notification.provider_credentials TO notification_app"
    )


def downgrade() -> None:
    # CASCADE drops the trigger automatically.
    op.execute("DROP TABLE IF EXISTS notification.provider_credentials CASCADE")
