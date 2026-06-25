# Migration Test Plan

QA runbook for validating **all service migrations through `head`** on a clean
Postgres before a release is considered production-ready. Until CI with
testcontainers is wired up by `ops`, these steps are run manually by the QA
engineer or DBA.

> **Heads are service-specific and move with every release.** Do not hardcode a
> revision number. Each service's `head` is whatever `alembic heads` reports for
> it; the checks below assert against that. The stored revision id is not a bare
> sequence number for every service — most heads are the full filename stem,
> while audit's head id happens to be the bare `0001`.
>
> **Maintenance:** revise this plan whenever a migration is added — at minimum
> the expected-tables list (§1), the reference-data counts (§8), and the
> system-actor count (§9).

_Last updated: 2026-06-25_

## Prerequisites

- Docker available (for ephemeral Postgres)
- Python 3.12 + `uv` installed
- Each service's dependencies installable via `uv pip install -e ".[dev]"`

## Environment setup

Start a throwaway Postgres 16 instance:

```bash
docker run --rm -d \
  --name lotsman-test-pg \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=lotsman \
  -p 5433:5432 \
  postgres:16

# Wait for readiness
until docker exec lotsman-test-pg pg_isready -U postgres; do sleep 1; done
```

Apply extensions and roles (as superuser):

```bash
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d lotsman \
  -f infra/postgres/init/00-extensions.sql

PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d lotsman \
  -f infra/postgres/init/01-schemas-and-roles.sql
```

Set passwords for app roles (test only, never in production):

```bash
for role in auth_app registry_app notification_app audit_app; do
  PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d lotsman \
    -c "ALTER ROLE $role PASSWORD 'testpassword';"
done
```

## Per-service migration test (repeat for each service)

Replace `<SERVICE>` with `auth`, `registry`, `notification`, `audit`.
Replace `<ROLE>` with `auth_app`, `registry_app`, `notification_app`, `audit_app`.

```bash
cd services/<SERVICE>-service

# Install deps (editable, with dev extras)
uv pip install -e ".[dev]" --system

# Set the DB URL (using superuser for migration runner; app role for runtime)
export <SERVICE_UPPER>_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5433/lotsman"

# Record the expected head before running. Ask Alembic for the real head
# revision id (the value stored in alembic_version.version_num) — do NOT derive
# it from the filename stem, because the repo uses a MIXED revision-id
# convention: most heads equal the full filename stem, but audit's head id is
# the bare "0001", not "0001_initial_audit_schema".
HEAD=$(alembic heads | awk '{print $1}')
echo "Expected head: $HEAD"
# As of 2026-06-25 the head revision ids are:
#   auth=0009_add_user_ui_font_scale, registry=0008_add_asset_status,
#   notification=0009_richer_email_templates, audit=0001.

# --- Round 1: upgrade to head (applies the full migration chain) ---
alembic upgrade head
# Expected: a "Running upgrade ..." line per migration, ending at $HEAD, no errors.

# --- Round 2: downgrade to base (reverses the whole chain) ---
alembic downgrade base
# Expected: a "Running downgrade ..." line per migration, then schema DROP, no errors.

# --- Round 3: upgrade again (idempotency / re-run safety) ---
alembic upgrade head
# Expected: same as Round 1, no errors.

# --- Verify alembic_version is in the service schema and matches head ---
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d lotsman \
  -c "SELECT version_num FROM <schema>.alembic_version;"
# Expected: one row, version_num == $HEAD. Note the stored value is the raw
# revision id: full stem for auth/registry/notification, bare "0001" for audit.
```

## Acceptance checks (after all four services are at head)

Run these checks against the same test Postgres instance.

### 1. All expected tables exist

```sql
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema IN ('auth','registry','notification','audit')
ORDER BY 1, 2;
```

Expected tables at head (head revision ids: auth=0009_add_user_ui_font_scale,
registry=0008_add_asset_status, notification=0009_richer_email_templates,
audit=0001). The list grows with each migration — re-derive it from
`information_schema` rather than trusting a snapshot if in doubt:

```
auth.backup_codes
auth.key_rotations
auth.login_attempts
auth.outbox
auth.outbox_dlq
auth.sessions
auth.totp_used_codes
auth.user_saved_filters
auth.users

registry.assets
registry.attachments
registry.document_types
registry.documents
registry.export_jobs
registry.outbox
registry.outbox_dlq
registry.tenant_preferences

notification.calendar_event_mappings
notification.calendar_subscriptions
notification.delivery_attempts
notification.idempotency
notification.message_templates
notification.outbox
notification.outbox_dlq
notification.provider_credentials
notification.user_notification_prefs
notification.user_notifications

audit.events              (partitioned parent)
audit.events_2026_05
audit.events_2026_06
... (through events_2027_05; 13 child partitions, see §3)
```

Each schema also carries an `alembic_version` table (excluded above; it is the
migration bookkeeping, not application data).

### 2. No cross-schema foreign keys

```sql
SELECT tc.table_schema, tc.table_name, kcu.column_name,
       ccu.table_schema AS foreign_schema, ccu.table_name AS foreign_table
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage ccu
    ON tc.constraint_name = ccu.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_schema != ccu.table_schema;
```

Expected: zero rows.

### 3. audit.events partitions — exactly 13 partitions

```sql
SELECT child.relname
FROM pg_inherits
JOIN pg_class parent ON pg_inherits.inhparent = parent.oid
JOIN pg_class child  ON pg_inherits.inhrelid  = child.oid
JOIN pg_namespace n  ON parent.relnamespace = n.oid
WHERE n.nspname = 'audit' AND parent.relname = 'events'
ORDER BY child.relname;
```

Expected: 13 rows (events_2026_05 through events_2027_05).

### 4. audit_app cannot UPDATE or DELETE audit.events

```bash
PGPASSWORD=testpassword psql -h localhost -p 5433 -U audit_app -d lotsman \
  -c "DELETE FROM audit.events WHERE occurred_at < now();"
```

Expected: `ERROR:  permission denied for table events`

```bash
PGPASSWORD=testpassword psql -h localhost -p 5433 -U audit_app -d lotsman \
  -c "UPDATE audit.events SET request_id='x' WHERE occurred_at < now();"
```

Expected: `ERROR:  permission denied for table events`

### 5. auth_app cannot access other schemas

```bash
PGPASSWORD=testpassword psql -h localhost -p 5433 -U auth_app -d lotsman \
  -c "SELECT 1 FROM registry.documents LIMIT 1;"
```

Expected: `ERROR:  permission denied for schema registry` or `for table documents`.

### 6. Partial unique index on auth.users.email

```sql
-- Insert a user
INSERT INTO auth.users (id, email, full_name, password_hash, totp_secret_enc, role)
VALUES (gen_random_uuid(), 'test@example.com', 'Test', 'hash', '\x00', 'viewer');

-- Duplicate email — should fail
INSERT INTO auth.users (id, email, full_name, password_hash, totp_secret_enc, role)
VALUES (gen_random_uuid(), 'TEST@EXAMPLE.COM', 'Test2', 'hash', '\x00', 'viewer');
-- Expected: unique violation

-- Soft-delete the first user
UPDATE auth.users SET deleted_at = now() WHERE email = 'test@example.com';

-- Now the same email is allowed for a new non-deleted user
INSERT INTO auth.users (id, email, full_name, password_hash, totp_secret_enc, role)
VALUES (gen_random_uuid(), 'test@example.com', 'Test3', 'hash', '\x00', 'viewer');
-- Expected: success (deleted_at IS NULL only for the new row)
```

### 7. ensure_partition function works

```sql
SELECT audit.ensure_partition('2027-06-01'::DATE);
-- Expected: 'CREATED: audit.events_2027_06'

SELECT audit.ensure_partition('2027-06-01'::DATE);
-- Expected: 'EXISTS: audit.events_2027_06'

-- Verify the new partition exists
SELECT relname FROM pg_class WHERE relname = 'events_2027_06';
-- Expected: one row

-- Cleanup
DROP TABLE audit.events_2027_06;
```

### 8. Reference data seeded

```sql
SELECT code, display_name FROM registry.document_types ORDER BY code;
```

Expected: 5 rows (audit_report, certification, contract, insurance, license).

```sql
SELECT channel, template_code, locale FROM notification.message_templates ORDER BY 1,2,3;
```

Expected: 9 rows (3 channels × 3 template codes, locale='ru'). Migration
`0009_richer_email_templates` rewrites the **subject/body** of the 3 email rows
(data-only, reversible) but does not add or remove rows — the count stays 9.

### 9. System actors seeded (by `alembic upgrade head`)

System actors are seeded by migration `auth/0002_seed_system_actors` as part of
`alembic upgrade head` — there is no separate `init/*.sql` step to run. Assert
the rows exist **after** the auth upgrade:

```bash
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d lotsman \
  -c "SELECT id, email, is_active FROM auth.users WHERE is_active = false ORDER BY email;"
```

Expected: 5 rows, all `is_active = false`, with the system-actor emails listed
in [`system-actors.md`](./system-actors.md). The canonical UUIDs are pinned in
both that doc and the migration.

## CI integration (ops to wire up)

Add a GitHub Actions job (or equivalent) per service:

```yaml
- name: Start Postgres
  uses: docker://postgres:16
  env:
    POSTGRES_PASSWORD: postgres
    POSTGRES_DB: lotsman

- name: Apply extensions and roles
  run: |
    psql ... -f infra/postgres/init/00-extensions.sql
    psql ... -f infra/postgres/init/01-schemas-and-roles.sql

- name: Run migrations (auth-service)
  working-directory: services/auth-service
  env:
    AUTH_DATABASE_URL: postgresql+asyncpg://postgres:postgres@localhost:5432/lotsman
  run: |
    uv pip install -e ".[dev]"
    alembic upgrade head
    alembic downgrade base
    alembic upgrade head

# Repeat for registry, notification, audit
```

Use `testcontainers` in pytest for the Python-level acceptance checks (items 2–9
above can be expressed as pytest fixtures that run against a fresh Postgres
started by `testcontainers.postgres.PostgresContainer`).

## Teardown

```bash
docker stop lotsman-test-pg
```
