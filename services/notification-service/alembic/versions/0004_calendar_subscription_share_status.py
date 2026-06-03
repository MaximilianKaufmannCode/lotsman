# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Add share_status columns to notification.calendar_subscriptions.

Revision ID: 0004_calendar_subscription_share_status
Revises: 0003_exchange_calendar_channel
Create Date: 2026-05-12

What & why
----------
When an admin adds a user to the calendar subscription whitelist, Лоцман now
automatically attempts to grant the user Reviewer permission on the shared
Exchange calendar folder via EWS (ADR-0005 §7, updated).

Three columns track this operation's lifecycle:

  share_status   — FSM: pending → granted | failed | not_attempted
                         revoked (on remove)
  share_granted_at — set when EWS permission_set succeeds
  share_error      — last EWS error message (sanitised, no credentials)

FSM transitions:
  INSERT            → 'pending'          (immediate, before EWS call)
  EWS grant OK      → 'granted'          + share_granted_at = now()
  EWS grant fail    → 'failed'           + share_error = <sanitised message>
  channel absent    → 'not_attempted'    (no exchange_calendar config found)
  POST retry-share  → back to 'pending'  then same grant flow
  DELETE (disable)  → 'revoked'          if EWS revoke succeeded
                    → 'failed'           if EWS revoke failed

Existing rows: migrated to 'not_attempted' — admin can trigger retry per user.
No EWS calls are made during migration (it runs in the DB context).

Downgrade pre-condition
-----------------------
No data pre-conditions required.  The downgrade simply drops the three columns.
Any in-flight 'pending' rows will be silently dropped — acceptable for rollback.
"""

from __future__ import annotations

from alembic import op

revision: str = "0004_calendar_subscription_share_status"
down_revision = "0003_exchange_calendar_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add share_status with a temporary default so existing rows are set.
    op.execute("""
        ALTER TABLE notification.calendar_subscriptions
            ADD COLUMN share_status TEXT NOT NULL DEFAULT 'not_attempted'
                CHECK (share_status IN ('pending','granted','failed','revoked','not_attempted'))
    """)
    op.execute(
        "COMMENT ON COLUMN notification.calendar_subscriptions.share_status IS "
        "'FSM: pending → granted | failed | not_attempted | revoked. "
        "Set to not_attempted on INSERT when exchange_calendar channel is absent. "
        "Retry via POST /admin/calendar-subscriptions/{user_id}/retry-share. "
        "Set to revoked when subscription is disabled and EWS revoke succeeded.'"
    )

    op.execute("""
        ALTER TABLE notification.calendar_subscriptions
            ADD COLUMN share_granted_at TIMESTAMPTZ
    """)
    op.execute(
        "COMMENT ON COLUMN notification.calendar_subscriptions.share_granted_at IS "
        "'Timestamp when EWS permission_set succeeded (granted → this field). NULL otherwise.'"
    )

    op.execute("""
        ALTER TABLE notification.calendar_subscriptions
            ADD COLUMN share_error TEXT
    """)
    op.execute(
        "COMMENT ON COLUMN notification.calendar_subscriptions.share_error IS "
        "'Last EWS error message (sanitised — no credentials). "
        "Populated on failed share grant/revoke attempts. Cleared on success.'"
    )

    # Update table comment to reflect the new columns.
    op.execute(
        "COMMENT ON TABLE notification.calendar_subscriptions IS "
        "'Whitelist of users who receive Exchange calendar events from Лоцман. "
        "user_id is a logical ref to auth.users(id) — no DB FK (cross-schema). "
        "Prefer enabled=false over DELETE to preserve audit history. "
        "share_status tracks automatic EWS Reviewer permission grant (ADR-0005 §7). "
        "See ADR-0005 §3.'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE notification.calendar_subscriptions "
        "DROP COLUMN IF EXISTS share_error"
    )
    op.execute(
        "ALTER TABLE notification.calendar_subscriptions "
        "DROP COLUMN IF EXISTS share_granted_at"
    )
    op.execute(
        "ALTER TABLE notification.calendar_subscriptions "
        "DROP COLUMN IF EXISTS share_status"
    )
