# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Initial audit schema — audit.events (partitioned), helper function,
monthly partitions 2026-05 through 2027-05.

Revision ID: 0001
Revises: (none — base)
Create Date: 2026-05-06

Design decisions:
- audit.events is a RANGE-partitioned table on occurred_at.
- The composite PK (id, occurred_at) is required by PostgreSQL: the partition
  key column must be part of every unique / primary key on the parent table.
- Partitions are named audit.events_YYYY_MM and cover [start, end) ranges
  where start = first day of month, end = first day of next month.
- Indexes are created on each partition individually by the helper function
  audit.ensure_partition(). The parent-table index declarations would create
  index templates but we spell them out explicitly per partition for
  visibility and to avoid autogenerate surprises.
- UPDATE and DELETE on audit.events are revoked from audit_app after table
  creation, making the log append-only at the DB level.
- audit-service has NO outbox table — it is a terminal event sink.
- The alembic_version table is stored in the audit schema
  (version_table_schema="audit" in env.py).
"""

from __future__ import annotations

from alembic import op

revision: str = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Months to pre-create: 2026-05 through 2027-05 inclusive (13 partitions).
# Format: (year, month) tuples.
_PARTITIONS: list[tuple[int, int]] = [
    (2026, 5),
    (2026, 6),
    (2026, 7),
    (2026, 8),
    (2026, 9),
    (2026, 10),
    (2026, 11),
    (2026, 12),
    (2027, 1),
    (2027, 2),
    (2027, 3),
    (2027, 4),
    (2027, 5),
]


def _partition_name(year: int, month: int) -> str:
    return f"events_{year}_{month:02d}"


def _partition_start(year: int, month: int) -> str:
    return f"{year}-{month:02d}-01"


def _partition_end(year: int, month: int) -> str:
    """First day of the following month."""
    if month == 12:
        return f"{year + 1}-01-01"
    return f"{year}-{month + 1:02d}-01"


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS audit")

    # ------------------------------------------------------------------
    # audit.events — partitioned parent table
    # SQLAlchemy autogenerate cannot emit PARTITION BY RANGE.
    # We use op.execute() for the full DDL.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit.events (
            id          UUID        NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            actor_id    UUID        NOT NULL,
            entity_type TEXT        NOT NULL,
            entity_id   UUID        NOT NULL,
            event_type  TEXT        NOT NULL,
            payload     JSONB       NOT NULL,
            request_id  TEXT,
            PRIMARY KEY (id, occurred_at)
        ) PARTITION BY RANGE (occurred_at)
    """)
    op.execute(
        "COMMENT ON TABLE audit.events IS "
        "'Append-only audit event log. Partitioned by RANGE (occurred_at), monthly. "
        "UPDATE and DELETE are revoked from audit_app.'"
    )
    op.execute(
        "COMMENT ON COLUMN audit.events.actor_id IS "
        "'UUID of acting user or system actor. See docs/db/system-actors.md.'"
    )
    op.execute(
        "COMMENT ON COLUMN audit.events.entity_type IS "
        "'Aggregate type: document | asset | user | session | delivery_attempt | ...'"
    )
    op.execute(
        "COMMENT ON COLUMN audit.events.event_type IS "
        "'Namespaced event type, e.g. registry.document.created.v1'"
    )

    # ------------------------------------------------------------------
    # Helper function: audit.ensure_partition(target_month date)
    # Creates a monthly partition idempotently.
    # Called by future migrations when adding new months.
    # See docs/db/audit-partitioning.md for full documentation.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION audit.ensure_partition(target_month DATE)
        RETURNS TEXT
        LANGUAGE plpgsql
        AS $$
        DECLARE
            yr       INT;
            mo       INT;
            part_name TEXT;
            start_dt  DATE;
            end_dt    DATE;
        BEGIN
            yr        := EXTRACT(YEAR  FROM target_month)::INT;
            mo        := EXTRACT(MONTH FROM target_month)::INT;
            part_name := 'events_' || yr || '_' || LPAD(mo::TEXT, 2, '0');
            start_dt  := DATE_TRUNC('month', target_month)::DATE;
            end_dt    := (DATE_TRUNC('month', target_month) + INTERVAL '1 month')::DATE;

            IF EXISTS (
                SELECT 1 FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'audit' AND c.relname = part_name
            ) THEN
                RETURN 'EXISTS: audit.' || part_name;
            END IF;

            EXECUTE format(
                'CREATE TABLE audit.%I PARTITION OF audit.events '
                'FOR VALUES FROM (%L) TO (%L)',
                part_name, start_dt, end_dt
            );

            -- Index: timeline query per entity (the primary audit-history query)
            EXECUTE format(
                'CREATE INDEX %I ON audit.%I (entity_type, entity_id, occurred_at DESC)',
                part_name || '_entity_idx', part_name
            );

            -- Index: "what did actor X do" (admin incident-triage query)
            EXECUTE format(
                'CREATE INDEX %I ON audit.%I (actor_id, occurred_at DESC)',
                part_name || '_actor_idx', part_name
            );

            -- Index: event_type filter (e.g. "all document.deleted events this month")
            EXECUTE format(
                'CREATE INDEX %I ON audit.%I (event_type, occurred_at DESC)',
                part_name || '_event_type_idx', part_name
            );

            RETURN 'CREATED: audit.' || part_name;
        END;
        $$
    """)
    op.execute(
        "COMMENT ON FUNCTION audit.ensure_partition(DATE) IS "
        "'Idempotently creates a monthly RANGE partition of audit.events with standard indexes. "
        "Call with the first day of any month: SELECT audit.ensure_partition(''2027-06-01'');'"
    )

    # ------------------------------------------------------------------
    # Pre-create partitions: 2026-05 through 2027-05 (13 months)
    # ------------------------------------------------------------------
    for year, month in _PARTITIONS:
        part_name = _partition_name(year, month)
        start_dt = _partition_start(year, month)
        end_dt = _partition_end(year, month)

        op.execute(
            f"CREATE TABLE IF NOT EXISTS audit.{part_name} "
            f"PARTITION OF audit.events "
            f"FOR VALUES FROM ('{start_dt}') TO ('{end_dt}')"
        )

        # Entity timeline query — used by audit-service GET /events?entity_id=...
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {part_name}_entity_idx "
            f"ON audit.{part_name} (entity_type, entity_id, occurred_at DESC)"
        )
        op.execute(
            f"COMMENT ON INDEX audit.{part_name}_entity_idx IS "
            f"'Entity audit history: SELECT ... WHERE entity_type=$1 AND entity_id=$2 ORDER BY occurred_at DESC.'"
        )

        # Actor query — admin incident triage
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {part_name}_actor_idx "
            f"ON audit.{part_name} (actor_id, occurred_at DESC)"
        )
        op.execute(
            f"COMMENT ON INDEX audit.{part_name}_actor_idx IS "
            f"'Actor audit trail: SELECT ... WHERE actor_id=$1 ORDER BY occurred_at DESC.'"
        )

        # Event type filter
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {part_name}_event_type_idx "
            f"ON audit.{part_name} (event_type, occurred_at DESC)"
        )
        op.execute(
            f"COMMENT ON INDEX audit.{part_name}_event_type_idx IS "
            f"'Event-type filter: SELECT ... WHERE event_type=$1 ORDER BY occurred_at DESC.'"
        )

    # ------------------------------------------------------------------
    # Revoke UPDATE and DELETE from audit_app — append-only enforcement
    # ------------------------------------------------------------------
    op.execute("GRANT SELECT, INSERT ON audit.events TO audit_app")
    op.execute("REVOKE UPDATE, DELETE ON audit.events FROM audit_app")

    # Grant the helper function to audit_app (needed by ARQ worker to
    # self-provision the next month's partition on startup)
    op.execute("GRANT EXECUTE ON FUNCTION audit.ensure_partition(DATE) TO audit_app")


def downgrade() -> None:
    # Dropping the schema CASCADE removes all partitions and the helper function.
    op.execute("DROP SCHEMA IF EXISTS audit CASCADE")
