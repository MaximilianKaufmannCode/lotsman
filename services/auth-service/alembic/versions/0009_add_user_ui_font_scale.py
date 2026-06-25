# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Add auth.users.ui_font_scale — per-user web-interface font-size preference.

Revision ID: 0009_add_user_ui_font_scale
Revises: 0008_add_user_saved_filters
Create Date: 2026-06-25

Design decisions:
- SCALAR-ON-USER, not a new table: the preference is exactly one value per user,
  so it rides the existing auth.users row and the already-wired GET/PATCH
  /v1/auth/me round-trip — no new endpoint, table, or BFF passthrough.
- SMALLINT percent (100 == current/default look). The SPA converts percent/100
  into the unitless CSS multiplier --app-font-scale. Integer end-to-end avoids
  any float-rounding drift between client, API and DB.
- ADDITIVE + NON-DESTRUCTIVE: ADD COLUMN with a constant DEFAULT and NOT NULL is
  a metadata-only change on PostgreSQL 11+ (no full-table rewrite, brief
  ACCESS EXCLUSIVE lock only). All existing rows instantly read 100 == today's
  exact rendering, so the feature is fully backward-compatible.
- CHECK (80..150) bounds the value defensively at the storage layer; the API and
  use-case clamp/validate too (never trust a client-supplied value — ADR-0003).

DOWNGRADE: safe and reversible — DROP CONSTRAINT + DROP COLUMN. Nothing else in
the schema references this column; downgrade loses only the stored preference.
"""

from __future__ import annotations

from alembic import op

revision: str = "0009_add_user_ui_font_scale"
down_revision = "0008_add_user_saved_filters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Metadata-only on PG11+: constant DEFAULT means existing rows are not
    # rewritten — they logically read 100 until explicitly updated.
    op.execute("""
        ALTER TABLE auth.users
            ADD COLUMN ui_font_scale SMALLINT NOT NULL DEFAULT 100
    """)

    op.execute("""
        ALTER TABLE auth.users
            ADD CONSTRAINT users_ui_font_scale_chk
            CHECK (ui_font_scale BETWEEN 80 AND 150)
    """)

    op.execute(
        "COMMENT ON COLUMN auth.users.ui_font_scale IS "
        "'Per-user web-interface font-size preference, as a percent of the base "
        "(100 = default / current look). The SPA maps percent/100 to the CSS "
        "--app-font-scale multiplier. Bounded 80..150 by users_ui_font_scale_chk.'"
    )


def downgrade() -> None:
    # Reversible and non-destructive: drop the CHECK then the column. No FK or
    # index references this column, so there are no cascade side effects.
    op.execute("ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS users_ui_font_scale_chk")
    op.execute("ALTER TABLE auth.users DROP COLUMN IF EXISTS ui_font_scale")
