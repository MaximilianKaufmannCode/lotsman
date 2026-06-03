# Database Documentation Index

All database design documentation for Лоцман lives here.

## Documents

| File | Topic |
|---|---|
| [system-actors.md](system-actors.md) | Well-known system-actor UUIDs used in audit.events.actor_id |
| [audit-partitioning.md](audit-partitioning.md) | Monthly RANGE partitioning on audit.events |
| [outbox-pattern.md](outbox-pattern.md) | Per-service transactional outbox convention and dispatcher contract |
| [migration-test-plan.md](migration-test-plan.md) | Manual + CI test plan for initial migrations |
| [saved-filters-and-indexes.md](saved-filters-and-indexes.md) | auth.user_saved_filters DDL + registry.documents index plan for v1.23.0 multi-level filtering |

## Schema quick reference

| Postgres schema | Owning service | App role |
|---|---|---|
| `auth` | auth-service | `auth_app` |
| `registry` | registry-service | `registry_app` |
| `notification` | notification-service | `notification_app` |
| `audit` | audit-service | `audit_app` |

All schemas live in the same Postgres database instance (`lotsman`), per ADR-0001.
No cross-schema foreign keys exist; cross-context references are bare UUIDs.

## Extension baseline

Installed in `infra/postgres/init/00-extensions.sql`:

- `pg_trgm` — trigram similarity for fuzzy search (assets.name, documents.number)
- `citext` — case-insensitive text (auth.users.email, auth.login_attempts.email)
- `pgcrypto` — `gen_random_uuid()` for UUIDv4 PKs

## Migration convention

Each service has its own independent Alembic project:

```
services/<name>/
  alembic.ini
  alembic/
    env.py          (async-first, reads <SERVICE>_DATABASE_URL from env)
    script.py.mako
    versions/
      0001_initial_<name>_schema.py
```

The `alembic_version` table is stored in each service's own schema
(`version_table_schema` set in `env.py`), so the four services do not
interfere with each other even though they share one Postgres database.
