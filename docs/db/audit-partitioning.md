# Audit Event Log Partitioning

`audit.events` is a PostgreSQL RANGE-partitioned table, partitioned on the
`occurred_at TIMESTAMPTZ` column, with one partition per calendar month.

## Why partition?

- **Query performance**: the primary read pattern is per-entity timeline
  (`WHERE entity_type=$1 AND entity_id=$2 ORDER BY occurred_at DESC LIMIT 50`).
  Partition pruning eliminates all partitions outside the queried time range
  with zero index scan overhead.
- **Retention**: future ADR may require dropping events older than N years.
  With monthly partitions, `DROP TABLE audit.events_2024_01` is instant
  (no row-level DELETE, no vacuum). Without partitioning, a table-level DELETE
  on hundreds of thousands of rows would require a reindex cycle.
- **Write throughput**: each partition is a physically separate heap; parallel
  INSERTs from the `audit-recorder` ARQ worker hit different pages.

## Partition naming convention

```
audit.events_YYYY_MM
```

Examples: `audit.events_2026_05`, `audit.events_2026_12`, `audit.events_2027_01`.

Each partition covers the half-open interval `[first_day_of_month, first_day_of_next_month)`.

## Standard indexes (created per partition)

Every partition gets three indexes, created by `audit.ensure_partition()`:

| Index suffix | Columns | Query it serves |
|---|---|---|
| `_entity_idx` | `(entity_type, entity_id, occurred_at DESC)` | UI "история изменений" panel: timeline per entity |
| `_actor_idx` | `(actor_id, occurred_at DESC)` | Admin incident triage: "what did actor X do?" |
| `_event_type_idx` | `(event_type, occurred_at DESC)` | Operational filter: "all document.deleted events this month" |

Indexes on the parent `audit.events` table are NOT created — PostgreSQL
does not inherit parent indexes to child partitions. Each partition's indexes
are owned by that partition.

## Helper function: `audit.ensure_partition(target_month DATE)`

The function creates a partition idempotently (returns immediately if it
already exists) and adds the three standard indexes.

```sql
-- Create the partition for August 2027:
SELECT audit.ensure_partition('2027-08-01'::DATE);

-- Idempotent — safe to call repeatedly:
SELECT audit.ensure_partition('2026-05-01');  -- returns 'EXISTS: audit.events_2026_05'
```

Source is in `services/audit-service/alembic/versions/0001_initial_audit_schema.py`.

`audit_app` role has EXECUTE on this function so the ARQ worker can self-provision
the next month's partition at startup if it is missing (belt-and-suspenders
alongside the migration-driven approach).

## Pre-created partitions

The `0001_initial_audit_schema` migration pre-creates 13 monthly partitions:

| Partition | From | To (exclusive) |
|---|---|---|
| `events_2026_05` | 2026-05-01 | 2026-06-01 |
| `events_2026_06` | 2026-06-01 | 2026-07-01 |
| `events_2026_07` | 2026-07-01 | 2026-08-01 |
| `events_2026_08` | 2026-08-01 | 2026-09-01 |
| `events_2026_09` | 2026-09-01 | 2026-10-01 |
| `events_2026_10` | 2026-10-01 | 2026-11-01 |
| `events_2026_11` | 2026-11-01 | 2026-12-01 |
| `events_2026_12` | 2026-12-01 | 2027-01-01 |
| `events_2027_01` | 2027-01-01 | 2027-02-01 |
| `events_2027_02` | 2027-02-01 | 2027-03-01 |
| `events_2027_03` | 2027-03-01 | 2027-04-01 |
| `events_2027_04` | 2027-04-01 | 2027-05-01 |
| `events_2027_05` | 2027-05-01 | 2027-06-01 |

## Ongoing maintenance strategy

### Day-one approach (current): migration-driven

When the last pre-created partition is about to be consumed (ideally 2 months
ahead), a new Alembic migration is added:

```python
# services/audit-service/alembic/versions/0002_audit_partitions_2027_06_to_2028_05.py
def upgrade():
    for year, month in [(2027,6),(2027,7),...,(2028,5)]:
        op.execute(f"SELECT audit.ensure_partition('{year}-{month:02d}-01'::DATE)")

def downgrade():
    for year, month in reversed([...]):
        op.execute(f"DROP TABLE IF EXISTS audit.events_{year}_{month:02d}")
```

This keeps partition creation versioned in git alongside the schema history.

### Future: pg_partman

If the team decides to automate partition creation without manual migrations,
`pg_partman` can take over. The migration-driven approach is preferred on day
one because:
1. It avoids an extra extension dependency.
2. It keeps all schema changes in the Alembic audit trail.
3. At our data volume (2-4 users), creating partitions 12 months ahead via
   a migration every ~6 months is zero operational burden.

A future ADR will evaluate `pg_partman` if the partition horizon shrinks due
to missed migrations or if the team grows large enough that cron-based
automation is clearly better.

## Append-only enforcement at DB level

After `audit.events` is created, the migration executes:

```sql
GRANT SELECT, INSERT ON audit.events TO audit_app;
REVOKE UPDATE, DELETE ON audit.events FROM audit_app;
```

The `01-schemas-and-roles.sql` init script sets `DEFAULT PRIVILEGES` to
`SELECT, INSERT` for `audit_app` in the audit schema, explicitly excluding
UPDATE and DELETE. This means future tables in the audit schema also inherit
the append-only grant by default.

The application role `audit_app` physically cannot issue `UPDATE` or `DELETE`
on `audit.events` — any such attempt returns `ERROR: permission denied`.

## Composite PK requirement

PostgreSQL 16 requires that the partition key column appear in every PRIMARY
KEY or UNIQUE constraint on the partitioned table. Hence:

```sql
PRIMARY KEY (id, occurred_at)
```

`id` alone would be logically sufficient, but `occurred_at` must be included.
This is documented in the model (`audit_service/db/models.py`) with a comment
to avoid future confusion during autogenerate review.
