# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Event notifications: in-app feed + widened delivery template codes (Phase 2, ADR-0011 §D1/§D5/§D6).

Additive only:
  - NEW table notification.user_notifications (in-app feed; populated by the
    notification-events consumer, read by the Phase 3 bell/feed UI).
  - WIDEN notification.delivery_attempts.template_code CHECK to allow the new
    lifecycle-event codes + 'digest' (existing rows are unaffected — the new set
    is a superset of the old one).

No data is modified or dropped.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-02
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_NEW_TEMPLATE_CODES = (
    "'pre_notice', 'in_day', 'overdue', "
    "'doc_created', 'doc_updated', 'doc_assigned', "
    "'doc_attachment', 'doc_archived', 'digest'"
)
_OLD_TEMPLATE_CODES = "'pre_notice', 'in_day', 'overdue'"


def upgrade() -> None:
    op.execute(
        f"""
        -- 1. In-app notification feed
        CREATE TABLE IF NOT EXISTS notification.user_notifications (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id       UUID NOT NULL,
            category      TEXT NOT NULL,
            document_id   UUID,
            actor_id      UUID,
            title         TEXT NOT NULL,
            body          TEXT NOT NULL DEFAULT '',
            is_read       BOOLEAN NOT NULL DEFAULT FALSE,
            email_pending BOOLEAN NOT NULL DEFAULT FALSE,
            emailed_at    TIMESTAMPTZ,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        COMMENT ON TABLE notification.user_notifications IS
            'In-app notification feed (ADR-0011). user_id/document_id/actor_id are logical refs, no DB FK.';

        CREATE INDEX IF NOT EXISTS user_notifications_user_created_idx
            ON notification.user_notifications (user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS user_notifications_unread_idx
            ON notification.user_notifications (user_id) WHERE is_read = FALSE;
        CREATE INDEX IF NOT EXISTS user_notifications_digest_idx
            ON notification.user_notifications (user_id) WHERE email_pending = TRUE;

        -- 2. Widen delivery_attempts.template_code CHECK (superset of old set)
        ALTER TABLE notification.delivery_attempts
            DROP CONSTRAINT IF EXISTS delivery_attempts_template_code_check;
        ALTER TABLE notification.delivery_attempts
            ADD CONSTRAINT delivery_attempts_template_code_check
            CHECK (template_code IN ({_NEW_TEMPLATE_CODES}));
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE notification.delivery_attempts
            DROP CONSTRAINT IF EXISTS delivery_attempts_template_code_check;
        ALTER TABLE notification.delivery_attempts
            ADD CONSTRAINT delivery_attempts_template_code_check
            CHECK (template_code IN ({_OLD_TEMPLATE_CODES}));
        DROP TABLE IF EXISTS notification.user_notifications;
        """
    )
