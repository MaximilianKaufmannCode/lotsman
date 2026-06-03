# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Add status column to registry.assets (active | liquidating | archived).

Revision ID: 0008_add_asset_status
Revises: 0007_add_filter_indexes
Create Date: 2026-05-26

Part of the registry-filters feature (v1.23.0). Gate decision 2026-05-26.
See docs/db/saved-filters-and-indexes.md §5 for full rationale.

Design decisions:
- Three-value enum expressed as a CHECK constraint (same pattern as
  documents.status in migration 0001).  The column is NOT NULL DEFAULT 'active'
  so the ADD COLUMN itself backfills all existing rows to 'active' in a single
  catalog-only change — no row rewrite at this scale.
- A second UPDATE then sets status='archived' for every row that is already
  soft-deleted (deleted_at IS NOT NULL).  This keeps the two signals consistent
  from day one without touching any active row.
- Partial index on (status) WHERE deleted_at IS NULL covers the primary filter
  path "show me all active / liquidating assets" in the registry grid.
- On prod (5 rows) the entire migration runs in milliseconds with no risk of
  lock contention.  No CONCURRENTLY needed for such a small table.

DOWNGRADE is fully reversible: drops the index, the check constraint, and the
column in that order (reverse of how they were added).

DO NOT run `alembic upgrade` from this file — migrations are applied by the
DevOps runbook, not ad-hoc.
"""

from __future__ import annotations

from alembic import op

revision: str = "0008_add_asset_status"
down_revision = "0007_add_filter_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Step 1: add the column with a safe NOT NULL default.
    #
    # PostgreSQL 11+ handles ADD COLUMN … NOT NULL DEFAULT as a metadata-only
    # change when the default is immutable (which 'active' is).  No table rewrite,
    # no row-level lock beyond the brief ACCESS EXCLUSIVE for the catalog update.
    # All existing rows — whether active or soft-deleted — receive 'active' as the
    # initial value; the next step corrects the soft-deleted ones.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE registry.assets
            ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'active'
    """)
    op.execute(
        "COMMENT ON COLUMN registry.assets.status IS "
        "'Functional status of the partner company: "
        "active = operating normally; "
        "liquidating = in wind-down / liquidation process; "
        "archived = no longer active, not expected to return. "
        "Distinct from deleted_at (soft-delete for recovery purposes). "
        "See docs/db/saved-filters-and-indexes.md §5 for the dual-signal model.'"
    )

    # ------------------------------------------------------------------
    # Step 2: add the CHECK constraint.
    #
    # Added BEFORE the backfill UPDATE so that the constraint is validated
    # against the final state in a single pass (PG validates NOT VALID
    # constraints lazily, but here we want eager validation on 5 rows — trivial).
    # All rows currently hold 'active' (from the DEFAULT), which satisfies the
    # constraint immediately.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE registry.assets
            ADD CONSTRAINT assets_status_check
                CHECK (status IN ('active', 'liquidating', 'archived'))
    """)

    # ------------------------------------------------------------------
    # Step 3: backfill — set status='archived' for every soft-deleted row.
    #
    # Rationale: deleted_at IS NOT NULL is the only proxy we have for "this
    # asset was intentionally removed".  The most conservative interpretation
    # is 'archived' (no longer active, not necessarily liquidating).  If a
    # specific row turns out to be 'liquidating' instead, an editor can correct
    # it through the normal UI after the migration.
    #
    # Rows with deleted_at IS NULL stay at 'active' — the DEFAULT already set
    # that correctly.
    # ------------------------------------------------------------------
    op.execute("""
        UPDATE registry.assets
            SET status = 'archived'
        WHERE deleted_at IS NOT NULL
    """)

    # ------------------------------------------------------------------
    # Step 4: partial index for the active-asset filter.
    #
    # Covers the registry grid filter "show assets by status" on the non-deleted
    # subset.  The partial predicate WHERE deleted_at IS NULL matches the query
    # shape used everywhere else in the codebase (consistent with
    # assets_name_trgm_idx and assets_name_active_uidx from migration 0001).
    #
    # At 5 prod rows this is instantaneous; no CONCURRENTLY needed.
    # When the table grows the index remains tiny because soft-deleted rows are
    # excluded.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS assets_status_active_idx
            ON registry.assets (status)
            WHERE deleted_at IS NULL
    """)
    op.execute(
        "COMMENT ON INDEX registry.assets_status_active_idx IS "
        "'Filter active assets by status (registry grid): "
        "SELECT ... WHERE status = $1 AND deleted_at IS NULL. "
        "Partial predicate matches the standard non-deleted filter used "
        "throughout the registry grid.'"
    )


def downgrade() -> None:
    # Reverse order: index → constraint → column.
    # IF NOT EXISTS / IF EXISTS guards make this idempotent in case of
    # partial failure on a previous downgrade attempt.
    op.execute("DROP INDEX IF EXISTS registry.assets_status_active_idx")
    op.execute("""
        ALTER TABLE registry.assets
            DROP CONSTRAINT IF EXISTS assets_status_check
    """)
    op.execute("""
        ALTER TABLE registry.assets
            DROP COLUMN IF EXISTS status
    """)
