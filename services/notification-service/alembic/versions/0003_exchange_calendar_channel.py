# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Extend notification channel support: add exchange_calendar + ics_feed channels,
calendar_subscriptions whitelist, and calendar_event_mappings tracking table.

Revision ID: 0003_exchange_calendar_channel
Revises: 0002_provider_credentials
Create Date: 2026-05-08

What & why
----------
ADR-0005 §1-§4, §9-§10 introduces Exchange Calendar as a 4th notification
channel, stored as a new channel type in the existing provider_credentials
table.  Two supporting tables are added:

  - calendar_subscriptions: whitelist of users who receive calendar events.
    Soft-disable (enabled=false) is preferred over DELETE so audit history is
    preserved.  user_id is a bare UUID — no DB FK (cross-schema convention).

  - calendar_event_mappings: tracks the Exchange ItemId / ChangeKey for each
    calendar event created in Exchange.  Per ADR-0005 §9 each document produces
    N calendar events (one per pre_notice_days value + one due-day event), so
    the PK is composite (document_id, notice_offset_days) rather than just
    document_id.  document_id is a bare UUID — no DB FK (cross-schema
    convention).

Operation 1: extend pc_channel_check
-------------------------------------
The constraint on notification.provider_credentials.channel is widened from
3 values to 5.  'ics_feed' is added alongside 'exchange_calendar' because the
ICS feed endpoint (ADR-0005 §10) is enabled/disabled and has its token stored
as a config_enc row — reusing the same credential-management infra.

Downgrade pre-condition
-----------------------
Before running `alembic downgrade -1` the caller MUST delete any rows that
would violate the narrowed CHECK:

    DELETE FROM notification.provider_credentials
    WHERE channel IN ('exchange_calendar', 'ics_feed');
    TRUNCATE notification.calendar_event_mappings;
    TRUNCATE notification.calendar_subscriptions;

Failing to do so will cause the ADD CONSTRAINT step to raise
  ERROR:  check constraint "pc_channel_check" of relation "provider_credentials"
  is violated by some row
and leave the schema in a partially downgraded state.

Style notes
-----------
All DDL is executed via op.execute() raw SQL to match the convention set in
0001 and 0002.  No SQLAlchemy DDL helpers are used.  The set_updated_at()
trigger function was installed by 0001 and is reused here.
"""

from __future__ import annotations

from alembic import op

revision: str = "0003_exchange_calendar_channel"
down_revision = "0002_provider_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Operation 1 — Extend pc_channel_check on provider_credentials
    # ------------------------------------------------------------------
    # DROP + ADD is the only safe pattern for CHECK constraints in Postgres
    # (no ALTER CONSTRAINT ... USING).  The window between DROP and ADD is
    # inside a single transaction, so no invalid rows can slip through.
    op.execute(
        "ALTER TABLE notification.provider_credentials "
        "DROP CONSTRAINT pc_channel_check"
    )
    op.execute("""
        ALTER TABLE notification.provider_credentials
            ADD CONSTRAINT pc_channel_check
                CHECK (channel IN ('email', 'telegram', 'dion', 'exchange_calendar', 'ics_feed'))
    """)
    op.execute(
        "COMMENT ON COLUMN notification.provider_credentials.channel IS "
        "'email | telegram | dion | exchange_calendar | ics_feed — unique per instance (pc_channel_unique). "
        "exchange_calendar config_enc holds EWS credentials (see ADR-0005 §2). "
        "ics_feed config_enc holds the shared feed token (ADR-0005 §10).'"
    )

    # ------------------------------------------------------------------
    # Operation 2 — notification.calendar_subscriptions
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE notification.calendar_subscriptions (
            user_id     UUID        NOT NULL,
            enabled     BOOLEAN     NOT NULL DEFAULT true,
            created_by  UUID        NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT calendar_subscriptions_pk PRIMARY KEY (user_id)
        )
    """)
    op.execute(
        "COMMENT ON TABLE notification.calendar_subscriptions IS "
        "'Whitelist of users who receive Exchange calendar events from Лоцман. "
        "user_id is a logical ref to auth.users(id) — no DB FK (cross-schema). "
        "Prefer enabled=false over DELETE to preserve audit history. See ADR-0005 §3.'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.calendar_subscriptions.user_id IS "
        "'Logical ref to auth.users(id). No DB FK — cross-schema convention.'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.calendar_subscriptions.created_by IS "
        "'UUID of the admin who added this subscription.'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.calendar_subscriptions.enabled IS "
        "'false = soft-disabled (opt-out); use this instead of DELETE.'"
    )

    op.execute("""
        CREATE TRIGGER calendar_subscriptions_set_updated_at
            BEFORE UPDATE ON notification.calendar_subscriptions
            FOR EACH ROW EXECUTE FUNCTION notification.set_updated_at()
    """)

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON notification.calendar_subscriptions TO notification_app"
    )

    # ------------------------------------------------------------------
    # Operation 3 — notification.calendar_event_mappings
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE notification.calendar_event_mappings (
            document_id        UUID        NOT NULL,
            notice_offset_days INT         NOT NULL,
            exchange_item_id   TEXT        NOT NULL,
            change_key         TEXT        NOT NULL,
            external_marker    TEXT        NOT NULL,
            last_synced_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            sync_state         TEXT        NOT NULL,
            last_error         TEXT,
            retry_count        INT         NOT NULL DEFAULT 0,
            CONSTRAINT calendar_event_mappings_pk
                PRIMARY KEY (document_id, notice_offset_days),
            CONSTRAINT cem_state_check
                CHECK (sync_state IN ('pending', 'synced', 'failed', 'dlq', 'deleted')),
            CONSTRAINT cem_offset_check
                CHECK (notice_offset_days >= 0)
        )
    """)
    op.execute(
        "COMMENT ON TABLE notification.calendar_event_mappings IS "
        "'Tracks Exchange ItemId/ChangeKey for each calendar event published by Лоцман. "
        "Composite PK (document_id, notice_offset_days) because each document generates "
        "N events — one per pre_notice_days value plus one due-day event (notice_offset_days=0). "
        "document_id is a logical ref to registry.documents(id) — no DB FK (cross-schema). "
        "See ADR-0005 §4 and §9.'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.calendar_event_mappings.document_id IS "
        "'Logical ref to registry.documents(id). No DB FK — cross-schema convention.'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.calendar_event_mappings.notice_offset_days IS "
        "'0 = due-day event; positive = pre-notice days before expiry (e.g. 30, 14, 7, 1).'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.calendar_event_mappings.exchange_item_id IS "
        "'EWS ItemId — opaque base64 string returned by Exchange on CreateItem.'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.calendar_event_mappings.change_key IS "
        "'EWS ChangeKey — concurrency token required for UpdateItem / DeleteItem. "
        "Must be refreshed after each successful write.'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.calendar_event_mappings.external_marker IS "
        r"'Copy of the Exchange extended_property value: \"lotsman:doc:<uuid>:offset:<N>\". "
        "Stored in both DB and Exchange for disaster-recovery mapping recovery. See ADR-0005 §13.'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.calendar_event_mappings.sync_state IS "
        "'pending | synced | failed | dlq | deleted'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.calendar_event_mappings.retry_count IS "
        "'Incremented by ARQ worker on each failed sync attempt. "
        "Row moves to dlq after max attempts (application-level threshold).'"
    )

    # Warm-up reconciliation query (ADR-0005 §12) and daily reconciliation:
    # SELECT ... WHERE sync_state IN ('pending','failed') AND last_synced_at < now() - interval
    op.execute("""
        CREATE INDEX cem_state_pending_idx
            ON notification.calendar_event_mappings (last_synced_at)
            WHERE sync_state IN ('pending', 'failed')
    """)
    op.execute(
        "COMMENT ON INDEX notification.cem_state_pending_idx IS "
        "'Warm-up reconciliation (ADR-0005 §12) and daily reconciliation (03:00 ARQ cron): "
        "SELECT document_id WHERE sync_state IN (pending,failed) AND last_synced_at < now()-interval.'"
    )

    # Disaster-recovery lookup by external_marker (ADR-0005 §13):
    # recover_mappings_from_exchange() FindItem → external_marker → INSERT/UPDATE mapping row
    op.execute("""
        CREATE UNIQUE INDEX cem_external_marker_idx
            ON notification.calendar_event_mappings (external_marker)
    """)
    op.execute(
        "COMMENT ON INDEX notification.cem_external_marker_idx IS "
        "'Disaster-recovery: recover_mappings_from_exchange() uses external_marker "
        "from Exchange extended_property to rebuild lost mapping rows. See ADR-0005 §13.'"
    )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON notification.calendar_event_mappings TO notification_app"
    )


def downgrade() -> None:
    # Pre-condition: caller must run before invoking this downgrade:
    #   DELETE FROM notification.provider_credentials
    #       WHERE channel IN ('exchange_calendar', 'ics_feed');
    #   TRUNCATE notification.calendar_event_mappings;
    #   TRUNCATE notification.calendar_subscriptions;
    # Skipping this will cause pc_channel_check ADD CONSTRAINT to fail on
    # existing rows with channel='exchange_calendar' or 'ics_feed'.
    #
    # CASCADE on DROP TABLE also removes the index and trigger automatically.

    op.execute("DROP TABLE IF EXISTS notification.calendar_event_mappings CASCADE")
    op.execute("DROP TABLE IF EXISTS notification.calendar_subscriptions CASCADE")

    op.execute(
        "ALTER TABLE notification.provider_credentials "
        "DROP CONSTRAINT pc_channel_check"
    )
    op.execute("""
        ALTER TABLE notification.provider_credentials
            ADD CONSTRAINT pc_channel_check
                CHECK (channel IN ('email', 'telegram', 'dion'))
    """)
    # Restore the original column comment.
    op.execute(
        "COMMENT ON COLUMN notification.provider_credentials.channel IS "
        "'email | telegram | dion — unique per instance (pc_channel_unique).'"
    )
