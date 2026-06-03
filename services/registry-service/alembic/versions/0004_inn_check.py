# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Add CHECK constraint on assets.inn for 10- or 12-digit format (Q6).

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-07

The full ФНС checksum validation is enforced at the application layer
(application/policies/inn_policy.py). The DB constraint is a sanity-check
on digit count only: 10 digits (юрлицо) or 12 digits (ИП), or NULL (optional).

Pattern: ^[0-9]{10}$|^[0-9]{12}$
"""

from __future__ import annotations

from alembic import op

revision: str = "0004"
down_revision: str = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE registry.assets
            ADD CONSTRAINT assets_inn_format_check
            CHECK (inn IS NULL OR inn ~ '^[0-9]{10}$' OR inn ~ '^[0-9]{12}$')
    """)
    op.execute(
        "COMMENT ON CONSTRAINT assets_inn_format_check ON registry.assets IS "
        "'Q6: INN must be NULL, 10 digits (юрлицо), or 12 digits (ИП). Checksum is enforced at app layer.'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE registry.assets DROP CONSTRAINT IF EXISTS assets_inn_format_check")
