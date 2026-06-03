# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Add auth.user_saved_filters — per-user named filter presets for the
document registry grid.

Revision ID: 0008_add_user_saved_filters
Revises: 0007_key_rotations
Create Date: 2026-05-26

Design decisions:
- No soft-delete: presets have no audit requirement.  Hard DELETE is fine;
  the FK ON DELETE CASCADE handles cleanup when a user is removed.
- Partial UNIQUE on (user_id) WHERE is_default = TRUE is the canonical
  PostgreSQL technique for "at most one default per owner" without any
  application-layer serialisation.
- UNIQUE on (user_id, name) prevents duplicate preset names per user.
- CHECK (jsonb_typeof(filter_json) = 'object') guards against accidentally
  storing a JSON array or scalar, which would break all downstream @> queries.
- updated_at is bumped by auth.set_updated_at() — reuses the trigger function
  declared in migration 0001_initial_auth_schema.  No new PL/pgSQL function
  is created here.
- Grant to auth_app is explicit: DEFAULT PRIVILEGES do not cover tables
  created after the initial grant window.

DOWNGRADE: safe — DROP TABLE CASCADE removes the table, indexes, and trigger.
No data in other auth tables references this table.
"""

from __future__ import annotations

from alembic import op

revision: str = "0008_add_user_saved_filters"
down_revision = "0007_key_rotations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE auth.user_saved_filters (
            id          UUID         NOT NULL DEFAULT gen_random_uuid(),
            user_id     UUID         NOT NULL,
            name        VARCHAR(100) NOT NULL,
            filter_json JSONB        NOT NULL DEFAULT '{}'::jsonb,
            is_default  BOOLEAN      NOT NULL DEFAULT FALSE,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),

            CONSTRAINT user_saved_filters_pk
                PRIMARY KEY (id),

            CONSTRAINT user_saved_filters_user_fk
                FOREIGN KEY (user_id)
                REFERENCES auth.users (id)
                ON DELETE CASCADE
                DEFERRABLE INITIALLY DEFERRED,

            CONSTRAINT user_saved_filters_name_length_chk
                CHECK (char_length(name) BETWEEN 1 AND 100),

            CONSTRAINT user_saved_filters_json_object_chk
                CHECK (jsonb_typeof(filter_json) = 'object')
        )
    """)

    op.execute(
        "COMMENT ON TABLE auth.user_saved_filters IS "
        "'Named filter presets saved by each user for the document registry grid. "
        "filter_json holds the serialised TanStack Table column-filter state.'"
    )
    op.execute(
        "COMMENT ON COLUMN auth.user_saved_filters.filter_json IS "
        "'Flat JSONB object (jsonb_typeof = object). Shape owned by frontend; "
        "recommend a version sentinel key: {\"v\": 1, ...}. "
        "Validated beyond type-check at the application layer only.'"
    )
    op.execute(
        "COMMENT ON COLUMN auth.user_saved_filters.is_default IS "
        "'At most one TRUE per user_id, enforced by partial unique index "
        "user_saved_filters_user_default_uidx (WHERE is_default = TRUE).'"
    )

    # Prevents duplicate preset names within a single user's set.
    op.execute("""
        CREATE UNIQUE INDEX user_saved_filters_user_name_uidx
            ON auth.user_saved_filters (user_id, name)
    """)
    op.execute(
        "COMMENT ON INDEX auth.user_saved_filters_user_name_uidx IS "
        "'Prevents two presets with the same name for the same user.'"
    )

    # Enforces at most one default preset per user.
    # Partial unique on a boolean column: only rows where is_default=TRUE
    # participate, so the index contains at most one entry per user_id.
    op.execute("""
        CREATE UNIQUE INDEX user_saved_filters_user_default_uidx
            ON auth.user_saved_filters (user_id)
            WHERE is_default = TRUE
    """)
    op.execute(
        "COMMENT ON INDEX auth.user_saved_filters_user_default_uidx IS "
        "'At-most-one-default constraint: partial unique on user_id WHERE is_default=TRUE. "
        "Attempting to insert a second default for the same user raises a unique violation.'"
    )

    # Hot query: GET /api/v1/filters?user_id=... — list all presets for a user,
    # newest first.
    op.execute("""
        CREATE INDEX user_saved_filters_user_created_idx
            ON auth.user_saved_filters (user_id, created_at DESC)
    """)
    op.execute(
        "COMMENT ON INDEX auth.user_saved_filters_user_created_idx IS "
        "'List presets for a user ordered newest-first: "
        "SELECT ... WHERE user_id=$1 ORDER BY created_at DESC.'"
    )

    # updated_at trigger — reuses auth.set_updated_at() from migration 0001.
    op.execute("""
        CREATE TRIGGER user_saved_filters_set_updated_at
            BEFORE UPDATE ON auth.user_saved_filters
            FOR EACH ROW EXECUTE FUNCTION auth.set_updated_at()
    """)

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON auth.user_saved_filters TO auth_app"
    )


def downgrade() -> None:
    # DROP TABLE CASCADE removes the table, all its indexes, and the trigger.
    # No foreign-key references from other tables point here, so CASCADE has
    # no side effects beyond the table itself.
    op.execute("DROP TABLE IF EXISTS auth.user_saved_filters")
