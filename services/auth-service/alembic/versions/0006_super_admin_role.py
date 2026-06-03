# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Add super_admin to auth.users role CHECK constraint.

Revision ID: 0006_super_admin_role
Revises: 0005_lockout_partial_index
Create Date: 2026-05-08

Upgrade:
  Drops the existing users_role_check constraint (which allows only
  'admin', 'editor', 'viewer') and replaces it with one that also
  allows 'super_admin'.

Downgrade:
  Re-narrows the constraint back to three roles.
  PRE-CONDITION: caller must run
    DELETE FROM auth.users WHERE role = 'super_admin';
  before running alembic downgrade, otherwise the constraint creation
  will fail for any existing super_admin rows.
"""

from __future__ import annotations

from alembic import op

revision: str = "0006_super_admin_role"
down_revision = "0005_lockout_partial_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS users_role_check")
    op.execute(
        "ALTER TABLE auth.users ADD CONSTRAINT users_role_check "
        "CHECK (role IN ('super_admin', 'admin', 'editor', 'viewer'))"
    )


def downgrade() -> None:
    # PRE-CONDITION: DELETE FROM auth.users WHERE role = 'super_admin';
    op.execute("ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS users_role_check")
    op.execute(
        "ALTER TABLE auth.users ADD CONSTRAINT users_role_check "
        "CHECK (role IN ('admin', 'editor', 'viewer'))"
    )
