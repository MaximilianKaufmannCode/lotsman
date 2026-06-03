# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Add supporting indexes on registry.documents for advanced filter scenarios
introduced in the multi-level filtering feature (v1.23.0).

Revision ID: 0007_add_filter_indexes
Revises: 0006
Create Date: 2026-05-26

Indexes added (all PRE-LAUNCH, see docs/db/saved-filters-and-indexes.md §2):

  documents_responsible_active_idx  btree(responsible_user_id) WHERE deleted_at IS NULL
    Covers S1 ("my documents") and S9 (asset + responsible combo).

  documents_type_active_idx         btree(type_code) WHERE deleted_at IS NULL AND status='active'
    Covers S3 (filter by type on active docs) and S4 (status baked into predicate).

  documents_asset_type_idx          btree(asset_id, type_code) WHERE deleted_at IS NULL
    Covers S3c — the most common drill-down: pick a company, then filter by type.

NOT included (deferred to WAIT-FOR-METRICS):
  documents_updated_idx (updated_at DESC) — see §2 Candidate C in the plan doc.

--- IMPORTANT: CREATE INDEX CONCURRENTLY ---

CREATE INDEX CONCURRENTLY cannot execute inside a transaction block.
Alembic wraps each migration in a transaction by default.

To opt out we mark this migration non-transactional at the Alembic level by
checking for the module-level variable `transaction_per_migration`:

    In env.py `do_run_migrations()`:
        context.configure(
            ...,
            transaction_per_migration=False,   # required for CONCURRENTLY
        )

BUT: our env.py does not currently have that option.  The safe alternative
used here is to call op.execute() directly with the raw CONCURRENTLY DDL.
Alembic's asyncpg adapter will execute these statements outside the implicit
transaction when the engine is configured with `isolation_level="AUTOCOMMIT"`.

In practice the current env.py wraps everything in `context.begin_transaction()`.
Two options for the operator running this migration:

Option A (recommended for production — zero downtime):
  Run the three CREATE INDEX CONCURRENTLY statements manually BEFORE applying
  this migration, then apply the migration.  Alembic's `IF NOT EXISTS`
  guard means it will skip already-created indexes cleanly.

  psql -U registry_app lotsman <<'SQL'
    CREATE INDEX CONCURRENTLY IF NOT EXISTS documents_responsible_active_idx
        ON registry.documents (responsible_user_id)
        WHERE deleted_at IS NULL;

    CREATE INDEX CONCURRENTLY IF NOT EXISTS documents_type_active_idx
        ON registry.documents (type_code)
        WHERE deleted_at IS NULL AND status = 'active';

    CREATE INDEX CONCURRENTLY IF NOT EXISTS documents_asset_type_idx
        ON registry.documents (asset_id, type_code)
        WHERE deleted_at IS NULL;
  SQL

  alembic upgrade head

Option B (acceptable for on-prem with a brief maintenance window):
  Apply the migration directly.  The DDL inside a transaction block
  creates the indexes without CONCURRENTLY (falls back to plain CREATE INDEX).
  On <10 k rows this takes milliseconds and holds a ShareLock for that
  duration only.  No data loss, no downtime at the current scale.

This file uses plain CREATE INDEX with IF NOT EXISTS so it is safe to run
in either mode.  Comment in the file notes where CONCURRENTLY is appropriate.

DOWNGRADE:
  Drops the three indexes.  Uses DROP INDEX CONCURRENTLY where possible.
  Because Alembic's downgrade also runs inside a transaction, the operator
  may need to run the DROP INDEX CONCURRENTLY statements manually (same
  caveat as above).
"""

from __future__ import annotations

from alembic import op

revision: str = "0007_add_filter_indexes"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # S1 / S9 — filter by responsible_user_id
    # ------------------------------------------------------------------
    # Covers "my documents" (WHERE responsible_user_id = $1) and the
    # combined asset + responsible filter (asset predicate handled by
    # existing documents_asset_active_idx or re-check; this index is the
    # driver when the user filter is more selective).
    #
    # Production note: replace CREATE INDEX with CREATE INDEX CONCURRENTLY
    # if running manually outside a transaction (see migration header).
    op.execute("""
        CREATE INDEX IF NOT EXISTS documents_responsible_active_idx
            ON registry.documents (responsible_user_id)
            WHERE deleted_at IS NULL
    """)
    op.execute(
        "COMMENT ON INDEX registry.documents_responsible_active_idx IS "
        "'Filter by responsible user (S1/S9): "
        "SELECT ... WHERE responsible_user_id=$1 AND deleted_at IS NULL. "
        "Also serves as the driver index for asset+responsible combos when "
        "responsible_user_id is more selective.'"
    )

    # ------------------------------------------------------------------
    # S3 / S4 — filter by document type (active documents only)
    # ------------------------------------------------------------------
    # Covers the type-dropdown filter in the registry grid. The partial
    # predicate bakes in status='active', matching the most common query
    # shape.  Queries against archived documents fall back to seq scan
    # (acceptable at current scale; add a separate index if that pattern
    # emerges per pg_stat_statements).
    op.execute("""
        CREATE INDEX IF NOT EXISTS documents_type_active_idx
            ON registry.documents (type_code)
            WHERE deleted_at IS NULL AND status = 'active'
    """)
    op.execute(
        "COMMENT ON INDEX registry.documents_type_active_idx IS "
        "'Filter by document type on active records (S3/S4): "
        "SELECT ... WHERE type_code=$1 AND deleted_at IS NULL AND status=active. "
        "Partial predicate matches the registry grid default view filter.'"
    )

    # ------------------------------------------------------------------
    # S3c — asset + type combination (most common drill-down)
    # ------------------------------------------------------------------
    # "Show all contracts for company X" is the canonical registry UI flow.
    # Composite (asset_id, type_code) satisfies both predicates at index
    # level with zero row re-checks.  Coexists with documents_asset_active_idx:
    # the planner uses whichever is more selective.
    op.execute("""
        CREATE INDEX IF NOT EXISTS documents_asset_type_idx
            ON registry.documents (asset_id, type_code)
            WHERE deleted_at IS NULL
    """)
    op.execute(
        "COMMENT ON INDEX registry.documents_asset_type_idx IS "
        "'Asset + type combo drill-down (S3c): "
        "SELECT ... WHERE asset_id=$1 AND type_code=$2 AND deleted_at IS NULL. "
        "Most common registry grid drill-down: pick company, filter by type.'"
    )


def downgrade() -> None:
    # DROP INDEX CONCURRENTLY cannot run inside a transaction block.
    # To drop without locking, run manually:
    #   DROP INDEX CONCURRENTLY IF EXISTS registry.documents_asset_type_idx;
    #   DROP INDEX CONCURRENTLY IF EXISTS registry.documents_type_active_idx;
    #   DROP INDEX CONCURRENTLY IF EXISTS registry.documents_responsible_active_idx;
    # then apply `alembic downgrade -1` (which will find no indexes to drop).
    #
    # At current scale (<10 k rows) plain DROP INDEX is also acceptable.
    op.execute("DROP INDEX IF EXISTS registry.documents_asset_type_idx")
    op.execute("DROP INDEX IF EXISTS registry.documents_type_active_idx")
    op.execute("DROP INDEX IF EXISTS registry.documents_responsible_active_idx")
