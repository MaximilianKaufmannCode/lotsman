# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Idempotency dedup key for in-app notifications (C1 fix, ADR-0011 follow-up).

Adds notification.user_notifications.dedup_key + a PARTIAL unique index on
(user_id, dedup_key) WHERE dedup_key IS NOT NULL, so at-least-once redelivery of
the same document event no longer creates duplicate feed rows (insert uses
ON CONFLICT DO NOTHING). Fully additive — existing rows keep dedup_key = NULL and
never collide (NULLs are excluded from the partial index).

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-02
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE notification.user_notifications
            ADD COLUMN IF NOT EXISTS dedup_key TEXT;

        CREATE UNIQUE INDEX IF NOT EXISTS user_notifications_dedup_uidx
            ON notification.user_notifications (user_id, dedup_key)
            WHERE dedup_key IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS notification.user_notifications_dedup_uidx;
        ALTER TABLE notification.user_notifications DROP COLUMN IF EXISTS dedup_key;
        """
    )
