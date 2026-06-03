# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Add auth.key_rotations table for manual key rotation tracking.

Revision ID: 0007_key_rotations
Revises: 0006_super_admin_role
Create Date: 2026-05-08

Tracks when each cryptographic key was last rotated by a super_admin.
This is a manual audit record — the system does not auto-rotate keys.

Known key_ids seeded from genesis (2026-05-06):
  RS256_JWT              — RS256 keypair for external JWT signing/verification
  INTERNAL_JWT_KEY_AUTH  — HS256 key for auth-service internal JWT
  INTERNAL_JWT_KEY_REGISTRY  — HS256 key for registry-service internal JWT
  INTERNAL_JWT_KEY_NOTIFICATION  — HS256 key for notification-service internal JWT
  INTERNAL_JWT_KEY_AUDIT — HS256 key for audit-service internal JWT
  INTERNAL_JWT_KEY_SYSTEM_CONTROL — HS256 key for system-control internal JWT
  TOTP_ENC_KEY           — Fernet key for TOTP secret encryption
  CHANNEL_ENC_KEY        — Fernet key for notification channel config encryption
"""

from __future__ import annotations

import textwrap

from alembic import op

revision: str = "0007_key_rotations"
down_revision = "0006_super_admin_role"
branch_labels = None
depends_on = None

# System actor UUID from 0002_seed_system_actors.py (ACTOR_SUPER_ADMIN_BOOTSTRAP placeholder).
# Use the outbox-dispatcher system actor as the "genesis" rotated_by for seed rows.
# This UUID matches auth.outbox_dispatcher in 0002_seed_system_actors.
_SYSTEM_ACTOR_UUID = "00000000-0000-0000-0000-000000000002"

# Project genesis date — keys existed from day one.
_GENESIS_DATE = "2026-05-06"


def upgrade() -> None:
    op.execute(
        textwrap.dedent("""
        CREATE TABLE auth.key_rotations (
            key_id      TEXT         PRIMARY KEY,
            rotated_at  TIMESTAMPTZ  NOT NULL,
            rotated_by  UUID         NOT NULL,
            note        TEXT
        );
        """)
    )

    # Grant to auth_app role (matches grants pattern from 0001_initial_auth_schema).
    op.execute("GRANT SELECT, INSERT, UPDATE ON auth.key_rotations TO auth_app")

    # Seed genesis rows so the key-rotations page has data from day one.
    op.execute(
        textwrap.dedent(f"""
        INSERT INTO auth.key_rotations (key_id, rotated_at, rotated_by, note)
        VALUES
            ('RS256_JWT',                     '{_GENESIS_DATE}', '{_SYSTEM_ACTOR_UUID}', 'Project genesis — initial key generation'),
            ('INTERNAL_JWT_KEY_AUTH',          '{_GENESIS_DATE}', '{_SYSTEM_ACTOR_UUID}', 'Project genesis — initial key generation'),
            ('INTERNAL_JWT_KEY_REGISTRY',      '{_GENESIS_DATE}', '{_SYSTEM_ACTOR_UUID}', 'Project genesis — initial key generation'),
            ('INTERNAL_JWT_KEY_NOTIFICATION',  '{_GENESIS_DATE}', '{_SYSTEM_ACTOR_UUID}', 'Project genesis — initial key generation'),
            ('INTERNAL_JWT_KEY_AUDIT',         '{_GENESIS_DATE}', '{_SYSTEM_ACTOR_UUID}', 'Project genesis — initial key generation'),
            ('INTERNAL_JWT_KEY_SYSTEM_CONTROL','{_GENESIS_DATE}', '{_SYSTEM_ACTOR_UUID}', 'Project genesis — initial key generation'),
            ('TOTP_ENC_KEY',                   '{_GENESIS_DATE}', '{_SYSTEM_ACTOR_UUID}', 'Project genesis — initial key generation'),
            ('CHANNEL_ENC_KEY',                '{_GENESIS_DATE}', '{_SYSTEM_ACTOR_UUID}', 'Project genesis — initial key generation');
        """)
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth.key_rotations")
