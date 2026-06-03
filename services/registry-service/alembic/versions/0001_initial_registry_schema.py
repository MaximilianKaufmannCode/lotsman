# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Initial registry schema — assets, document_types, documents, attachments,
export_jobs, outbox, outbox_dlq.

Revision ID: 0001
Revises: (none — base)
Create Date: 2026-05-06

Design decisions:
- All cross-service references (responsible_user_id, created_by, updated_by,
  requested_by) are bare UUIDs — no DB FK to auth schema (iron rule #1).
- GIN trigram indexes on assets.name and documents.number support the
  pg_trgm fuzzy search queries from the BFF.
- Partial unique index on assets.name WHERE deleted_at IS NULL enforces
  company-name uniqueness among active records.
- document_types has no deleted_at: it is managed by admin migrations or
  make seed; archiving documents of a deprecated type is handled at app level.
- attachments.document_id uses ON DELETE CASCADE within the registry schema.
- export_jobs has a compound index on (status, created_at) for the cleanup
  job and for the "my pending exports" endpoint.
"""

from __future__ import annotations

from alembic import op

revision: str = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS registry")

    # ------------------------------------------------------------------
    # updated_at trigger function
    # ------------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION registry.set_updated_at()
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
    # registry.assets
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS registry.assets (
            id         UUID        NOT NULL DEFAULT gen_random_uuid(),
            name       TEXT        NOT NULL,
            inn        VARCHAR(12),
            notes      TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at TIMESTAMPTZ,
            CONSTRAINT assets_pk PRIMARY KEY (id)
        )
    """)
    op.execute(
        "COMMENT ON TABLE registry.assets IS "
        "'Partner companies (партнёрские компании). Soft-deleted via deleted_at.'"
    )
    op.execute(
        "COMMENT ON COLUMN registry.assets.inn IS "
        "'Russian tax ID (ИНН). Optional, no uniqueness enforced.'"
    )

    # Fuzzy name search — the primary search path for finding companies.
    op.execute("""
        CREATE INDEX IF NOT EXISTS assets_name_trgm_idx
            ON registry.assets USING GIN (name gin_trgm_ops)
            WHERE deleted_at IS NULL
    """)
    op.execute(
        "COMMENT ON INDEX registry.assets_name_trgm_idx IS "
        "'pg_trgm GIN index for fuzzy company-name search: SELECT ... WHERE name % $1 AND deleted_at IS NULL.'"
    )

    # Partial unique on name — prevents duplicate company names in active records.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS assets_name_active_uidx
            ON registry.assets (name)
            WHERE deleted_at IS NULL
    """)
    op.execute(
        "COMMENT ON INDEX registry.assets_name_active_uidx IS "
        "'Unique company name among non-deleted assets.'"
    )

    op.execute("""
        CREATE TRIGGER assets_set_updated_at
            BEFORE UPDATE ON registry.assets
            FOR EACH ROW EXECUTE FUNCTION registry.set_updated_at()
    """)

    # ------------------------------------------------------------------
    # registry.document_types
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS registry.document_types (
            code               VARCHAR(64) NOT NULL,
            display_name       TEXT        NOT NULL,
            pre_notice_days    INTEGER[]   NOT NULL DEFAULT '{30,7,1}',
            notify_in_day      BOOLEAN     NOT NULL DEFAULT TRUE,
            overdue_every_days INTEGER     NOT NULL DEFAULT 7,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT document_types_pk PRIMARY KEY (code)
        )
    """)
    op.execute(
        "COMMENT ON TABLE registry.document_types IS "
        "'Document type catalog: contract, license, audit_report, etc.'"
    )
    op.execute(
        "COMMENT ON COLUMN registry.document_types.pre_notice_days IS "
        "'Days before expiry_date to send pre-notice notifications, e.g. {30,7,1}.'"
    )
    op.execute(
        "COMMENT ON COLUMN registry.document_types.overdue_every_days IS "
        "'Repeat overdue notification every N days until archived.'"
    )

    op.execute("""
        CREATE TRIGGER document_types_set_updated_at
            BEFORE UPDATE ON registry.document_types
            FOR EACH ROW EXECUTE FUNCTION registry.set_updated_at()
    """)

    # ------------------------------------------------------------------
    # registry.documents
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS registry.documents (
            id                  UUID        NOT NULL DEFAULT gen_random_uuid(),
            asset_id            UUID        NOT NULL,
            type_code           VARCHAR(64) NOT NULL,
            number              TEXT,
            issue_date          DATE,
            expiry_date         DATE,
            responsible_user_id UUID,
            status              VARCHAR(20) NOT NULL DEFAULT 'active',
            notes               TEXT,
            created_by          UUID        NOT NULL,
            updated_by          UUID        NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at          TIMESTAMPTZ,
            CONSTRAINT documents_pk           PRIMARY KEY (id),
            CONSTRAINT documents_status_check CHECK (status IN ('active', 'archived')),
            CONSTRAINT documents_asset_fk     FOREIGN KEY (asset_id)
                REFERENCES registry.assets (id)
                ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT documents_type_fk      FOREIGN KEY (type_code)
                REFERENCES registry.document_types (code)
                ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED
        )
    """)
    op.execute(
        "COMMENT ON TABLE registry.documents IS "
        "'Core document records per partner company. Soft-deleted via deleted_at.'"
    )
    op.execute(
        "COMMENT ON COLUMN registry.documents.responsible_user_id IS "
        "'Logical ref to auth.users(id). No DB FK. Nullified by registry-orphan-watch consumer.'"
    )
    op.execute(
        "COMMENT ON COLUMN registry.documents.created_by IS "
        "'Logical ref to auth.users(id) — actor who created this record.'"
    )
    op.execute(
        "COMMENT ON COLUMN registry.documents.updated_by IS "
        "'Logical ref to auth.users(id) — actor who last updated this record.'"
    )

    # Hot query: "all active documents for an asset" (document-list view)
    op.execute("""
        CREATE INDEX IF NOT EXISTS documents_asset_active_idx
            ON registry.documents (asset_id)
            WHERE deleted_at IS NULL
    """)
    op.execute(
        "COMMENT ON INDEX registry.documents_asset_active_idx IS "
        "'Document list per asset: SELECT ... WHERE asset_id=$1 AND deleted_at IS NULL.'"
    )

    # Hot query: "documents expiring in the next N days" — notification scheduler
    op.execute("""
        CREATE INDEX IF NOT EXISTS documents_expiry_active_idx
            ON registry.documents (expiry_date)
            WHERE deleted_at IS NULL AND status = 'active'
    """)
    op.execute(
        "COMMENT ON INDEX registry.documents_expiry_active_idx IS "
        "'Notification scheduler hot path: SELECT ... WHERE expiry_date BETWEEN $1 AND $2 AND status=active.'"
    )

    # Fuzzy document-number search
    op.execute("""
        CREATE INDEX IF NOT EXISTS documents_number_trgm_idx
            ON registry.documents USING GIN (number gin_trgm_ops)
            WHERE deleted_at IS NULL
    """)
    op.execute(
        "COMMENT ON INDEX registry.documents_number_trgm_idx IS "
        "'pg_trgm GIN index for fuzzy document-number search.'"
    )

    op.execute("""
        CREATE TRIGGER documents_set_updated_at
            BEFORE UPDATE ON registry.documents
            FOR EACH ROW EXECUTE FUNCTION registry.set_updated_at()
    """)

    # ------------------------------------------------------------------
    # registry.attachments
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS registry.attachments (
            id                UUID        NOT NULL DEFAULT gen_random_uuid(),
            document_id       UUID        NOT NULL,
            original_filename TEXT        NOT NULL,
            mime_type         VARCHAR(128) NOT NULL,
            size_bytes        BIGINT      NOT NULL,
            sha256            CHAR(64)    NOT NULL,
            storage_path      TEXT        NOT NULL,
            created_by        UUID        NOT NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT attachments_pk          PRIMARY KEY (id),
            CONSTRAINT attachments_document_fk FOREIGN KEY (document_id)
                REFERENCES registry.documents (id)
                ON DELETE CASCADE
                DEFERRABLE INITIALLY DEFERRED
        )
    """)
    op.execute(
        "COMMENT ON TABLE registry.attachments IS "
        "'File attachment metadata. Bytes stored on volume; path in storage_path.'"
    )
    op.execute(
        "COMMENT ON COLUMN registry.attachments.sha256 IS "
        "'Hex-encoded SHA-256 for integrity verification on download.'"
    )
    op.execute(
        "COMMENT ON COLUMN registry.attachments.created_by IS 'Logical ref to auth.users(id).'"
    )

    # Hot query: "all attachments for a document"
    op.execute("""
        CREATE INDEX IF NOT EXISTS attachments_document_idx
            ON registry.attachments (document_id)
    """)
    op.execute(
        "COMMENT ON INDEX registry.attachments_document_idx IS "
        "'Attachment list per document: SELECT ... WHERE document_id=$1.'"
    )

    # ------------------------------------------------------------------
    # registry.export_jobs
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS registry.export_jobs (
            id           UUID        NOT NULL DEFAULT gen_random_uuid(),
            requested_by UUID        NOT NULL,
            status       VARCHAR(20) NOT NULL DEFAULT 'pending',
            file_path    TEXT,
            error        TEXT,
            expires_at   TIMESTAMPTZ,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT export_jobs_pk           PRIMARY KEY (id),
            CONSTRAINT export_jobs_status_check CHECK (status IN ('pending', 'running', 'done', 'failed'))
        )
    """)
    op.execute("COMMENT ON TABLE registry.export_jobs IS 'Xlsx export task tracking.'")
    op.execute(
        "COMMENT ON COLUMN registry.export_jobs.requested_by IS 'Logical ref to auth.users(id).'"
    )
    op.execute(
        "COMMENT ON COLUMN registry.export_jobs.expires_at IS "
        "'When the export file should be purged from disk.'"
    )

    # Hot query: "pending and running jobs" (worker polling) + cleanup job
    op.execute("""
        CREATE INDEX IF NOT EXISTS export_jobs_status_created_idx
            ON registry.export_jobs (status, created_at)
            WHERE status IN ('pending', 'running')
    """)
    op.execute(
        "COMMENT ON INDEX registry.export_jobs_status_created_idx IS "
        "'Export worker polling: WHERE status IN (pending, running). Also used by cleanup job.'"
    )

    op.execute("""
        CREATE TRIGGER export_jobs_set_updated_at
            BEFORE UPDATE ON registry.export_jobs
            FOR EACH ROW EXECUTE FUNCTION registry.set_updated_at()
    """)

    # ------------------------------------------------------------------
    # registry.outbox
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS registry.outbox (
            id            UUID        NOT NULL DEFAULT gen_random_uuid(),
            occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            dispatched_at TIMESTAMPTZ,
            topic         TEXT        NOT NULL,
            payload       JSONB       NOT NULL,
            CONSTRAINT outbox_pk PRIMARY KEY (id)
        )
    """)
    op.execute(
        "COMMENT ON TABLE registry.outbox IS 'Transactional outbox for registry domain events.'"
    )
    op.execute(
        "COMMENT ON COLUMN registry.outbox.topic IS "
        "'Redis Stream key: registry.documents | registry.assets | registry.document_types'"
    )

    op.execute("""
        CREATE INDEX IF NOT EXISTS outbox_undispatched_idx
            ON registry.outbox (occurred_at)
            WHERE dispatched_at IS NULL
    """)
    op.execute(
        "COMMENT ON INDEX registry.outbox_undispatched_idx IS "
        "'outbox-dispatcher polling query: SELECT ... FOR UPDATE SKIP LOCKED WHERE dispatched_at IS NULL.'"
    )

    # ------------------------------------------------------------------
    # registry.outbox_dlq
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS registry.outbox_dlq (
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
        "COMMENT ON TABLE registry.outbox_dlq IS "
        "'Dead-letter queue for registry.outbox rows that exhausted all dispatch retries.'"
    )

    # ------------------------------------------------------------------
    # Seed reference data for document_types
    # (seed-loader makes a second pass via make seed for user-visible data;
    # these catalog rows are structural defaults needed at migration time)
    # ------------------------------------------------------------------
    op.execute("""
        INSERT INTO registry.document_types
            (code, display_name, pre_notice_days, notify_in_day, overdue_every_days)
        VALUES
            ('contract',      'Договор',                '{30,7,1}', TRUE,  7),
            ('license',       'Лицензия',               '{60,30,7}', TRUE, 14),
            ('audit_report',  'Аудиторский отчёт',      '{30,7,1}', TRUE,  7),
            ('insurance',     'Страховой полис',        '{30,7,1}', TRUE,  7),
            ('certification', 'Сертификат соответствия','{60,30,7}', TRUE, 14)
        ON CONFLICT (code) DO NOTHING
    """)

    # ------------------------------------------------------------------
    # Table-level grants
    # ------------------------------------------------------------------
    for table in (
        "assets",
        "document_types",
        "documents",
        "attachments",
        "export_jobs",
        "outbox",
        "outbox_dlq",
    ):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON registry.{table} TO registry_app")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS registry CASCADE")
