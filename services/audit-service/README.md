# audit-service

The immutable event log for Лоцман. Every state-changing event from every other service ends up here. The service is a **terminal sink**: it consumes all streams, writes append-only rows to `audit.events`, and exposes a read-only HTTP API for the UI panel "история изменений" (change history).

It never publishes events of its own and never calls any other service over HTTP. The Postgres role `audit_app` has only `INSERT` and `SELECT` on `audit.events` — `UPDATE` and `DELETE` are revoked at the database level.

---

## Owns

**Postgres schema**: `audit` (app role: `audit_app`)

**Domain entities**: `AuditEvent` (append-only, monthly RANGE partitions)

---

## Public surface

Operational endpoints:

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Liveness — returns `{"status": "ok"}` |
| GET | `/readyz` | Readiness — checks Postgres and Redis Streams reachability |
| GET | `/metrics` | Prometheus metrics (text format) |

Read-only query API (`api/v1/audit.py`):

| Method | Path | Access | Filters & limit |
|---|---|---|---|
| GET | `/api/v1/audit/events` | any authenticated actor | `entity_type`, `entity_id`, `actor`, `event_type`, `from`, `to`, `limit` (≤ 200, default 50) |
| GET | `/api/v1/audit/system` | `super_admin` only | system-relevant events (policy violations, `system.command.*`, key rotation); `from`, `to`, `actor`, `type`, `limit` (≤ 100) |

Results are ordered `occurred_at DESC` and capped by `limit` (no cursor pagination at this stage).

OpenAPI: http://localhost:8004/api/docs (when running locally).

---

## Events published

**None.** `audit-service` is a sink only.

## Events consumed

From **all** publisher streams (consumer group `audit-recorder`). Stream names are the source of truth in `config.py` (`stream_keys`) — they **must match the `topic` column of each `<service>.outbox` table**, which the dispatchers write. A mismatch makes events silently bypass `audit.events`, so treat this list carefully.

| Stream | Source service |
|---|---|
| `auth.user` | auth-service |
| `auth.session` | auth-service |
| `auth.invite` | auth-service |
| `auth.invitation` | auth-service |
| `registry.documents` | registry-service |
| `registry.assets` | registry-service |
| `registry.document_types` | registry-service |
| `registry.imports` | registry-service |
| `registry.preferences` | registry-service |
| `registry.exports` | registry-service |
| `notification.calendar` | notification-service |
| `notification.channel` | notification-service |
| `notification.email` | notification-service |
| `notification.prefs` | notification-service |

`asset` is the internal code/DB name for the user-facing entity **Компания** (Company); `registry.assets` carries its lifecycle events.

The consumer reads with `XREADGROUP` (batch size 10, block 1 s) and performs an idempotency check on `envelope.id` before inserting. See [ADR-0002 §A and §C](../../docs/adr/0002-service-boundaries.md).

> **Do not use as stream names:** `auth.users` / `auth.sessions` (plural) were defaults until 2026-05-22 — but publishers write the singular `auth.user` / `auth.session`, so every `auth.*` and several `registry.*` events silently bypassed the audit log until the fix. `notification.deliveries` is kept in the config only for backward-compat and is **currently unused**: notification migrated to 2-segment `notification.<aggregate>` topics (2026-05-25, after the `notification.outbox` double-prefix fix), which is why the live notification streams are `calendar` / `channel` / `email` / `prefs`.

---

## Local dev

Required environment variables:

| Variable | Example | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://audit_app:pw@localhost/lotsman` | Async SQLAlchemy DSN |
| `INTERNAL_JWT_KEY_AUDIT` | `dev-secret-at-least-32-chars-long-xx` | Per-service HS256 key for verifying internal JWTs (min 32 chars) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis Streams source |

Run standalone (Postgres and Redis must be up):

```bash
cd services/audit-service
DATABASE_URL=postgresql+asyncpg://audit_app:pw@localhost/lotsman \
INTERNAL_JWT_KEY_AUDIT=dev-secret-at-least-32-chars-long-xx \
uv run uvicorn audit_service.main:app --reload --port 8004
```

Or via Docker Compose:

```bash
docker compose -f infra/compose.dev.yml up audit-svc --build
```

Port mapping: `127.0.0.1:8004 → container:8000`.

---

## Tests

```bash
uv run pytest services/audit-service/tests -q
```

---

## Directory layout

```
services/audit-service/
├── alembic/
│   └── versions/           Includes monthly partition setup for audit.events
├── alembic.ini
├── src/audit_service/
│   ├── domain/             Domain errors (AuditDomainError, …)
│   ├── application/        Port protocols (AuditEventRepository)
│   ├── db/                 SQLAlchemy models (AuditEvent, partitioned)
│   ├── infrastructure/
│   │   ├── db/             Async session factory
│   │   └── consumer/       XREADGROUP consumer worker (audit-recorder)
│   ├── api/
│   │   └── v1/             FastAPI read-only routers (audit.py)
│   ├── config.py           stream_keys, consumer_group, batch settings
│   └── main.py
├── tests/
│   └── unit/
├── Dockerfile
└── pyproject.toml
```

Note: `audit-service` has **no** `outbox/` directory — it is a sink and publishes nothing.

---

## Migrations

```bash
cd services/audit-service
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

The `alembic_version` table lives in the `audit` schema. The initial migration creates the partitioned `audit.events` table. See [docs/db/audit-partitioning.md](../../docs/db/audit-partitioning.md).

---

*Last updated: 2026-06-25*
