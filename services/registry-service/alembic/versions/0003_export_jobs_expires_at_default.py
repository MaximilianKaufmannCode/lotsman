# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Add default for export_jobs.expires_at and partial index for cron query (Q8).

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-07

Changes:
  1. Set DEFAULT NOW() + interval '24 hours' on export_jobs.expires_at.
     This ensures every job row has a TTL from the moment it is inserted,
     even if the worker crashes before setting expires_at on completion.
     The worker overwrites expires_at to completed_at + 24h on success.

  2. Add partial index on (expires_at) WHERE purged_at IS NULL — optimises
     the purge_expired_exports cron query:
       SELECT ... WHERE expires_at < NOW() AND file_path IS NOT NULL AND status = 'done'

     Note: purged_at column not present in the ORM model; we use file_path IS NOT NULL
     as the proxy for "not yet purged" (purge task sets file_path = NULL).
"""

from __future__ import annotations

from alembic import op

revision: str = "0003"
down_revision: str = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Default expires_at to NOW + 24h on insert
    op.execute("""
        ALTER TABLE registry.export_jobs
            ALTER COLUMN expires_at
            SET DEFAULT NOW() + INTERVAL '24 hours'
    """)
    op.execute(
        "COMMENT ON COLUMN registry.export_jobs.expires_at IS "
        "'Q8: TTL 24h. Defaults to NOW()+24h; overwritten by worker to completed_at+24h.'"
    )

    # 2. Partial index for the hourly purge cron
    op.execute("""
        CREATE INDEX IF NOT EXISTS export_jobs_expires_unpurged_idx
            ON registry.export_jobs (expires_at)
            WHERE file_path IS NOT NULL AND status = 'done'
    """)
    op.execute(
        "COMMENT ON INDEX registry.export_jobs_expires_unpurged_idx IS "
        "'Hourly purge cron: SELECT WHERE expires_at < NOW() AND file_path IS NOT NULL AND status=done.'"
    )

    # 3. Add filters JSONB column to store export parameters snapshot
    op.execute("""
        ALTER TABLE registry.export_jobs
            ADD COLUMN IF NOT EXISTS filters JSONB NOT NULL DEFAULT '{}'::jsonb
    """)
    op.execute(
        "COMMENT ON COLUMN registry.export_jobs.filters IS "
        "'Snapshot of filter+sort+visible_columns at job submission time (Q2 snapshot semantics).'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS registry.export_jobs_expires_unpurged_idx")
    op.execute("ALTER TABLE registry.export_jobs ALTER COLUMN expires_at DROP DEFAULT")
    op.execute("ALTER TABLE registry.export_jobs DROP COLUMN IF EXISTS filters")
