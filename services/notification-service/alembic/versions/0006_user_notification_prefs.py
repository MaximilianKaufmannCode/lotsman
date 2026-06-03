# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Per-user notification preferences (notifications-expansion Phase 1, ADR-0011 §D2).

Adds notification.user_notification_prefs — one row per auth.users(id) (logical
ref, no DB FK, consistent with the rest of the notification schema). Stores the
master switch, "suppress my own actions" flag, the email delivery mode, and a
JSONB category×channel matrix.

Fully ADDITIVE: a brand-new table only. No existing table or row is touched, so
this is safe to apply on the live DB. Absent row → code defaults (see
user_notification_prefs domain defaults), so the feature works before any user
saves settings.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-01
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


# Default category matrix — mirrors the requirements §2.
# Kept in SQL as the column default so a row inserted with only user_id is sane.
_DEFAULT_CATEGORIES = (
    '{'
    '"doc_created":{"in_app":true,"email":false},'
    '"doc_updated":{"in_app":true,"email":false},'
    '"doc_assigned":{"in_app":true,"email":true},'
    '"doc_attachment":{"in_app":true,"email":false},'
    '"doc_archived":{"in_app":true,"email":false},'
    '"deadline":{"in_app":true,"email":true},'
    '"asset":{"in_app":false,"email":false}'
    '}'
)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS notification.user_notification_prefs (
            user_id       UUID PRIMARY KEY,
            enabled       BOOLEAN NOT NULL DEFAULT TRUE,
            suppress_own  BOOLEAN NOT NULL DEFAULT TRUE,
            email_mode    TEXT NOT NULL DEFAULT 'digest',
            categories    JSONB NOT NULL DEFAULT '{_DEFAULT_CATEGORIES}'::jsonb,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT user_notification_prefs_email_mode_check
                CHECK (email_mode IN ('instant', 'digest', 'off'))
        );

        COMMENT ON TABLE notification.user_notification_prefs IS
            'Per-user notification preferences (ADR-0011). user_id is a logical ref to auth.users(id), no DB FK.';
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notification.user_notification_prefs;")
