# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""seed system actors

Inserts well-known system-actor users into auth.users so that audit.events rows
produced by automated workers can reference a stable, recognizable actor ID.

These rows are NOT login accounts:
  - is_active = false (cannot log in)
  - password_hash = 'SYSTEM' (sentinel; argon2id never produces this string)
  - totp_secret_enc = b'\\x00' (no real secret)

Canonical UUIDs are pinned here AND in docs/db/system-actors.md.
The shared kernel module `lotsman_shared.actors` MUST mirror these exact values.

Why a data migration (not infra/postgres/init/*.sql):
The init/ scripts run BEFORE Alembic creates auth.users — they have no table to
insert into. A data migration runs in the deterministic post-DDL order and is
versioned with the schema, so QA / CI test it together with the rest.

Revision ID: 0002_seed_system_actors
Revises: 0001_initial_auth_schema
Create Date: 2026-05-06

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_seed_system_actors"
down_revision = "0001_initial_auth_schema"
branch_labels = None
depends_on = None


SYSTEM_ACTORS: list[dict[str, str]] = [
    {
        "id": "018f4e2a-dead-7000-8000-000000000001",
        "email": "outbox-dispatcher@system.lotsman",
        "full_name": "Outbox Dispatcher",
    },
    {
        "id": "018f4e2a-dead-7000-8000-000000000002",
        "email": "notification-scheduler@system.lotsman",
        "full_name": "Notification Scheduler",
    },
    {
        "id": "018f4e2a-dead-7000-8000-000000000003",
        "email": "audit-recorder@system.lotsman",
        "full_name": "Audit Recorder",
    },
    {
        "id": "018f4e2a-dead-7000-8000-000000000004",
        "email": "system-migrator@system.lotsman",
        "full_name": "System Migrator",
    },
    {
        "id": "018f4e2a-dead-7000-8000-000000000005",
        "email": "seed-loader@system.lotsman",
        "full_name": "Seed Loader",
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    insert_stmt = sa.text(
        """
        INSERT INTO auth.users (
            id, email, full_name, password_hash, totp_secret_enc,
            role, is_active, created_at, updated_at
        ) VALUES (
            CAST(:id AS uuid),
            CAST(:email AS citext),
            :full_name,
            'SYSTEM',
            decode('00', 'hex'),
            'viewer',
            false,
            now(),
            now()
        )
        ON CONFLICT (id) DO NOTHING
        """
    )
    for actor in SYSTEM_ACTORS:
        conn.execute(insert_stmt, actor)


def downgrade() -> None:
    conn = op.get_bind()
    actor_ids = [actor["id"] for actor in SYSTEM_ACTORS]
    conn.execute(
        sa.text("DELETE FROM auth.users WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": actor_ids},
    )
