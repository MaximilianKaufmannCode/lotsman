# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Initial notification schema — delivery_attempts, message_templates,
idempotency, outbox, outbox_dlq.

Revision ID: 0001
Revises: (none — base)
Create Date: 2026-05-06

Design decisions:
- document_id and user_id are bare UUIDs — no DB FKs (cross-schema).
- retry_count as INTEGER on delivery_attempts supports the retry backoff logic.
- message_templates has a (channel, template_code, locale) unique constraint
  so the app can upsert templates safely.
- idempotency uses a composite PK (provider, idempotency_key); no TTL at DB
  level — the application cleans up old rows periodically.
- The (status, scheduled_at) index on delivery_attempts covers the scheduler's
  hot polling query.
"""

from __future__ import annotations

from alembic import op

revision: str = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS notification")

    # ------------------------------------------------------------------
    # updated_at trigger function
    # ------------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION notification.set_updated_at()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$
    """)

    # ------------------------------------------------------------------
    # notification.delivery_attempts
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS notification.delivery_attempts (
            id            UUID        NOT NULL DEFAULT gen_random_uuid(),
            document_id   UUID        NOT NULL,
            user_id       UUID        NOT NULL,
            channel       VARCHAR(20) NOT NULL,
            template_code VARCHAR(30) NOT NULL,
            scheduled_at  TIMESTAMPTZ NOT NULL,
            sent_at       TIMESTAMPTZ,
            status        VARCHAR(20) NOT NULL DEFAULT 'pending',
            error         TEXT,
            retry_count   INTEGER     NOT NULL DEFAULT 0,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT delivery_attempts_pk              PRIMARY KEY (id),
            CONSTRAINT delivery_attempts_channel_check   CHECK (channel IN ('email', 'telegram', 'dion')),
            CONSTRAINT delivery_attempts_template_check  CHECK (
                template_code IN ('pre_notice', 'in_day', 'overdue')
            ),
            CONSTRAINT delivery_attempts_status_check    CHECK (status IN ('pending', 'sent', 'failed'))
        )
    """)
    op.execute(
        "COMMENT ON TABLE notification.delivery_attempts IS "
        "'Scheduled, sent, and failed notification delivery records.'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.delivery_attempts.document_id IS "
        "'Logical ref to registry.documents(id). No DB FK.'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.delivery_attempts.user_id IS "
        "'Logical ref to auth.users(id). No DB FK.'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.delivery_attempts.retry_count IS "
        "'Incremented by the scheduler on each retry attempt.'"
    )

    # Scheduler hot query: pending deliveries ordered by scheduled_at
    op.execute("""
        CREATE INDEX IF NOT EXISTS delivery_attempts_pending_idx
            ON notification.delivery_attempts (scheduled_at)
            WHERE status = 'pending'
    """)
    op.execute(
        "COMMENT ON INDEX notification.delivery_attempts_pending_idx IS "
        "'Notification scheduler: SELECT ... WHERE status=pending ORDER BY scheduled_at ASC.'"
    )

    # Lookup: "all deliveries for a document" (notification history panel)
    op.execute("""
        CREATE INDEX IF NOT EXISTS delivery_attempts_document_idx
            ON notification.delivery_attempts (document_id, scheduled_at DESC)
    """)
    op.execute(
        "COMMENT ON INDEX notification.delivery_attempts_document_idx IS "
        "'GET /api/v1/deliveries?document_id=... — history panel per document.'"
    )

    op.execute("""
        CREATE TRIGGER delivery_attempts_set_updated_at
            BEFORE UPDATE ON notification.delivery_attempts
            FOR EACH ROW EXECUTE FUNCTION notification.set_updated_at()
    """)

    # ------------------------------------------------------------------
    # notification.message_templates
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS notification.message_templates (
            id            UUID        NOT NULL DEFAULT gen_random_uuid(),
            channel       VARCHAR(20) NOT NULL,
            template_code VARCHAR(30) NOT NULL,
            locale        VARCHAR(10) NOT NULL DEFAULT 'ru',
            subject       TEXT,
            body_md       TEXT        NOT NULL,
            variables     JSONB       NOT NULL DEFAULT '{}',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT message_templates_pk             PRIMARY KEY (id),
            CONSTRAINT message_templates_channel_check  CHECK (channel IN ('email', 'telegram', 'dion')),
            CONSTRAINT message_templates_uniq           UNIQUE (channel, template_code, locale)
        )
    """)
    op.execute(
        "COMMENT ON TABLE notification.message_templates IS "
        "'Per-channel, per-locale notification templates. body_md uses Jinja2 syntax.'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.message_templates.variables IS "
        "'JSON schema or list of expected template variable names, for validation.'"
    )

    op.execute("""
        CREATE TRIGGER message_templates_set_updated_at
            BEFORE UPDATE ON notification.message_templates
            FOR EACH ROW EXECUTE FUNCTION notification.set_updated_at()
    """)

    # Seed default templates (ru locale)
    op.execute("""
        INSERT INTO notification.message_templates
            (channel, template_code, locale, subject, body_md, variables)
        VALUES
            ('email', 'pre_notice', 'ru',
             'Лоцман: срок актуализации документа через {{days_left}} дн.',
             E'Уважаемый(ая) {{full_name}},\\n\\nДокумент **{{document_number}}** ({{document_type}})\\nкомпании **{{asset_name}}** истекает **{{expiry_date}}** (через {{days_left}} дн.).\\n\\nОтветственный: {{responsible_name}}\\n\\n— Лоцман',
             '{"days_left": "int", "full_name": "str", "document_number": "str", "document_type": "str", "asset_name": "str", "expiry_date": "str", "responsible_name": "str"}'::jsonb),
            ('email', 'in_day', 'ru',
             'Лоцман: документ истекает сегодня',
             E'Уважаемый(ая) {{full_name}},\\n\\nДокумент **{{document_number}}** истекает **сегодня**.\\n\\n— Лоцман',
             '{"full_name": "str", "document_number": "str"}'::jsonb),
            ('email', 'overdue', 'ru',
             'Лоцман: документ просрочен {{days_overdue}} дн.',
             E'Уважаемый(ая) {{full_name}},\\n\\nДокумент **{{document_number}}** просрочен на {{days_overdue}} дн.\\n\\n— Лоцман',
             '{"full_name": "str", "document_number": "str", "days_overdue": "int"}'::jsonb),
            ('telegram', 'pre_notice', 'ru', NULL,
             E'*Лоцман*: документ {{document_number}} ({{asset_name}}) истекает через {{days_left}} дн. ({{expiry_date}})',
             '{"document_number": "str", "asset_name": "str", "days_left": "int", "expiry_date": "str"}'::jsonb),
            ('telegram', 'in_day', 'ru', NULL,
             E'*Лоцман*: документ {{document_number}} ({{asset_name}}) истекает *сегодня*',
             '{"document_number": "str", "asset_name": "str"}'::jsonb),
            ('telegram', 'overdue', 'ru', NULL,
             E'*Лоцман*: документ {{document_number}} ({{asset_name}}) просрочен на {{days_overdue}} дн.',
             '{"document_number": "str", "asset_name": "str", "days_overdue": "int"}'::jsonb),
            ('dion', 'pre_notice', 'ru', NULL,
             E'Лоцман: документ {{document_number}} истекает через {{days_left}} дн.',
             '{"document_number": "str", "days_left": "int"}'::jsonb),
            ('dion', 'in_day', 'ru', NULL,
             E'Лоцман: документ {{document_number}} истекает сегодня',
             '{"document_number": "str"}'::jsonb),
            ('dion', 'overdue', 'ru', NULL,
             E'Лоцман: документ {{document_number}} просрочен на {{days_overdue}} дн.',
             '{"document_number": "str", "days_overdue": "int"}'::jsonb)
        ON CONFLICT (channel, template_code, locale) DO NOTHING
    """)

    # ------------------------------------------------------------------
    # notification.idempotency
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS notification.idempotency (
            provider        VARCHAR(20) NOT NULL,
            idempotency_key TEXT        NOT NULL,
            first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            result_payload  JSONB,
            CONSTRAINT idempotency_pk PRIMARY KEY (provider, idempotency_key)
        )
    """)
    op.execute(
        "COMMENT ON TABLE notification.idempotency IS "
        "'Provider-level idempotency keys to prevent duplicate sends on worker retry.'"
    )
    op.execute(
        "COMMENT ON COLUMN notification.idempotency.first_seen_at IS "
        "'When this key was first recorded. Used by cleanup job to purge old entries.'"
    )

    # Cleanup query: purge old idempotency records
    op.execute("""
        CREATE INDEX IF NOT EXISTS idempotency_first_seen_idx
            ON notification.idempotency (first_seen_at)
    """)
    op.execute(
        "COMMENT ON INDEX notification.idempotency_first_seen_idx IS "
        "'Cleanup job: DELETE FROM idempotency WHERE first_seen_at < now() - interval.'"
    )

    # ------------------------------------------------------------------
    # notification.outbox
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS notification.outbox (
            id            UUID        NOT NULL DEFAULT gen_random_uuid(),
            occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            dispatched_at TIMESTAMPTZ,
            topic         TEXT        NOT NULL,
            payload       JSONB       NOT NULL,
            CONSTRAINT outbox_pk PRIMARY KEY (id)
        )
    """)
    op.execute(
        "COMMENT ON TABLE notification.outbox IS "
        "'Transactional outbox for notification domain events (notification.deliveries stream).'"
    )

    op.execute("""
        CREATE INDEX IF NOT EXISTS outbox_undispatched_idx
            ON notification.outbox (occurred_at)
            WHERE dispatched_at IS NULL
    """)
    op.execute(
        "COMMENT ON INDEX notification.outbox_undispatched_idx IS "
        "'outbox-dispatcher polling query: SELECT ... FOR UPDATE SKIP LOCKED WHERE dispatched_at IS NULL.'"
    )

    # ------------------------------------------------------------------
    # notification.outbox_dlq
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS notification.outbox_dlq (
            id          UUID        NOT NULL DEFAULT gen_random_uuid(),
            occurred_at TIMESTAMPTZ NOT NULL,
            topic       TEXT        NOT NULL,
            payload     JSONB       NOT NULL,
            failed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_error  TEXT        NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT outbox_dlq_pk PRIMARY KEY (id)
        )
    """)
    op.execute(
        "COMMENT ON TABLE notification.outbox_dlq IS "
        "'Dead-letter queue for notification.outbox rows that exhausted all dispatch retries.'"
    )

    # ------------------------------------------------------------------
    # Table-level grants
    # ------------------------------------------------------------------
    for table in (
        "delivery_attempts",
        "message_templates",
        "idempotency",
        "outbox",
        "outbox_dlq",
    ):
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON notification.{table} TO notification_app"
        )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS notification CASCADE")
