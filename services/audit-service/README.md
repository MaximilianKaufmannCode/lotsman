# audit-service

The immutable event log for Лоцман. Every state-changing event from every other service ends up here. The service is a **terminal sink**: it consumes all streams, writes append-only rows to `audit.events`, and exposes a read-only HTTP API for the UI panel "история изменений" (change history).

It never publishes events of its own and never calls any other service over HTTP. The Postgres role `audit_app` has only `INSERT` and `SELECT` on `audit.events` — `UPDATE` and `DELETE` are revoked at the database level.

---

## Owns

**Postgres schema**: `audit` (app role: `audit_app`)

**Domain entities**: `AuditEvent` (append-only, monthly RANGE partitions)

---

## Public surface

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Liveness — returns `{"status": "ok"}` |
| GET | `/readyz` | Readiness — checks Postgres and Redis Streams reachability |
| GET | `/metrics` | Prometheus metrics (text format) |

Business endpoint (`/api/v1/events?entity_type=document&entity_id=…&limit=50`) is implemented during `the audit-history feature`.

OpenAPI: http://localhost:8004/api/docs (when running locally).

---

## Events published

**None.** `audit-service` is a sink only.

## Events consumed

From **all** publisher streams (consumer group `audit-recorder`):

| Stream | Source service |
|---|---|
| `auth.users` | auth-service |
| `auth.sessions` | auth-service |
| `registry.documents` | registry-service |
| `registry.assets` | registry-service |
| `registry.document_types` | registry-service |
| `notification.deliveries` | notification-service |

The consumer reads with `XREADGROUP` (batch size 10, block 1 s). It performs an idempotency check on `envelope.id` before inserting. See [ADR-0002 §A and §C](../../docs/adr/0002-service-boundaries.md).

---

## Local dev

Required environment variables:

| Variable | Example | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://audit_app:pw@localhost/lotsman` | Async SQLAlchemy DSN |
| `INTERNAL_JWT_SECRET` | `dev-secret-32-chars-minimum` | Shared HS256 key |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis Streams source |

Run standalone (Postgres and Redis must be up):

```bash
cd services/audit-service
DATABASE_URL=postgresql+asyncpg://audit_app:pw@localhost/lotsman \
INTERNAL_JWT_SECRET=dev-secret \
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
│   ├── domain/             AuditEvent entity and errors
│   ├── application/        Use cases (record_event), port protocols
│   ├── infrastructure/
│   │   ├── db/             SQLAlchemy models, session factory
│   │   └── consumer/       ARQ XREADGROUP consumer worker (audit-recorder)
│   ├── api/
│   │   └── v1/             FastAPI read-only routers
│   ├── config.py           Includes stream_keys, consumer_group, batch settings
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

*Last updated: 2026-05-06*
