# Migration Test Plan

This document is the QA pickup for validating the four initial Alembic
migrations before they are considered production-ready. Until CI with
testcontainers is wired up by `ops`, these steps are executed
manually by the QA engineer or DBA.

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

# --- Round 1: upgrade ---
alembic upgrade head
# Expected: "Running upgrade -> 0001, Initial <service> schema"

# --- Round 2: downgrade ---
alembic downgrade base
# Expected: "Running downgrade 0001 -> <base>, ..." then schema DROP

# --- Round 3: upgrade again (idempotency / re-run safety) ---
alembic upgrade head
# Expected: same as Round 1, no errors

# --- Verify alembic_version is in the service schema ---
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d lotsman \
  -c "SELECT version_num FROM <schema>.alembic_version;"
# Expected: one row with value "0001"
```

## Acceptance checks (after all four services migrated)

Run these checks against the same test Postgres instance.

### 1. All expected tables exist

```sql
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema IN ('auth','registry','notification','audit')
ORDER BY 1, 2;
```

Expected tables:

```
auth.login_attempts
auth.outbox
auth.outbox_dlq
auth.sessions
auth.users

registry.assets
registry.attachments
registry.document_types
registry.documents
registry.export_jobs
registry.outbox
registry.outbox_dlq

notification.delivery_attempts
notification.idempotency
notification.message_templates
notification.outbox
notification.outbox_dlq

audit.events              (partitioned parent)
audit.events_2026_05
audit.events_2026_06
... (through events_2027_05)
```

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

Expected: 9 rows (3 channels × 3 template codes, locale='ru').

### 9. System actors seeded (after running 02-system-actors.sql)

```bash
PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d lotsman \
  -f infra/postgres/init/02-system-actors.sql

PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d lotsman \
  -c "SELECT id, email, is_active FROM auth.users WHERE is_active = false ORDER BY email;"
```

Expected: 5 rows with the system-actor emails listed in `docs/db/system-actors.md`.

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
