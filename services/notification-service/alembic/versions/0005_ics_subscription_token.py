# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Adds per-user ics_feed_token to calendar_subscriptions.

Replaces (or supplements) the EWS-grant flow with a personal ICS feed URL
per subscriber: each row gets a random URL-safe token. The public endpoint
GET /api/v1/calendar/feed/{token}.ics resolves the token to a single
subscription and serves the same set of registry events that EWS would
have written to the shared mailbox.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-14
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE notification.calendar_subscriptions
            ADD COLUMN IF NOT EXISTS ics_feed_token TEXT;

        UPDATE notification.calendar_subscriptions
           SET ics_feed_token = encode(gen_random_bytes(24), 'hex')
         WHERE ics_feed_token IS NULL;

        ALTER TABLE notification.calendar_subscriptions
            ALTER COLUMN ics_feed_token SET NOT NULL;

        CREATE UNIQUE INDEX IF NOT EXISTS calendar_subscriptions_ics_feed_token_uidx
            ON notification.calendar_subscriptions (ics_feed_token);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS notification.calendar_subscriptions_ics_feed_token_uidx;
        ALTER TABLE notification.calendar_subscriptions
            DROP COLUMN IF EXISTS ics_feed_token;
        """
    )
