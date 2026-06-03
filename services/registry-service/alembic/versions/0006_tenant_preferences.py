# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Adds registry.tenant_preferences key/value JSONB store for tenant-wide UI
settings (column order, future visibility defaults, etc.).

Single-tenant on-prem: one row per setting, key is the setting name.
Audit trail lives in the outbox via RegistryColumnOrderChanged event.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-13
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS registry.tenant_preferences (
            key         TEXT PRIMARY KEY,
            value       JSONB NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by  UUID NOT NULL
        );

        CREATE TRIGGER tenant_preferences_set_updated_at
            BEFORE UPDATE ON registry.tenant_preferences
            FOR EACH ROW EXECUTE FUNCTION registry.set_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS registry.tenant_preferences;")
