# notification-service

Manages delivery rules, schedules notifications, and dispatches reminders to Email (SMTP), Telegram (Bot API), and Dion, and publishes document deadlines to calendars — a shared Microsoft Exchange/Outlook calendar over EWS and a tokenized iCalendar (ICS) feed that any calendar app can subscribe to. It never polls `registry-service` over HTTP — it derives everything it needs from the event streams it consumes. When a document is created or updated, `notification-service` reacts to the event, applies the matching `DeliveryRule`, and schedules a `DeliveryAttempt`. An ARQ scheduler worker fires the attempt at the right time and handles retries.

Provider credentials (SMTP password, Telegram bot token, Dion API key, Exchange/EWS service account) live exclusively in this service's schema, keeping the security blast radius separate from registry data.

---

## Owns

**Postgres schema**: `notification` (app role: `notification_app`)

**Domain entities**: `DeliveryRule`, `DeliveryAttempt`, `MessageTemplate`, `ProviderCredential` (encrypted at rest), `RetryPolicy`

---

## Public surface

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Liveness — returns `{"status": "ok"}` |
| GET | `/readyz` | Readiness — checks Postgres and Redis reachability |
| GET | `/metrics` | Prometheus metrics (text format) |
| GET | `/api/v1/calendar/feed/{token}.ics` | Public ICS feed — the token is the auth; one VEVENT + VALARM per document |
| GET·POST·DELETE | `/api/v1/admin/calendar-subscriptions` | Calendar subscribers + automatic EWS "Reviewer" grant/revoke (ADR-0005) |

Business endpoints (`/api/v1/rules`, `/api/v1/deliveries`, `/api/v1/deliveries/{id}/resend`) are implemented during `the notifications feature`.

OpenAPI: http://localhost:8003/api/docs (when running locally).

---

## Events published

Published to Redis Stream `notification.deliveries`:
- `notification.delivery.scheduled.v1`
- `notification.delivery.sent.v1`
- `notification.delivery.failed.v1`

## Events consumed

From Redis Stream `registry.documents` (consumer group `notification-scheduler`):
- `registry.document.created.v1`
- `registry.document.updated.v1`
- `registry.document.archived.v1`

From Redis Stream `auth.users` (consumer group `notification-contact-sync`):
- `auth.user.deactivated.v1`
- `auth.user.updated.v1` — updates cached contact channel (email, Telegram username)

All events use the canonical envelope from `lotsman_shared.envelope`. See [ADR-0002 §A and §C](../../docs/adr/0002-service-boundaries.md).

---

## Local dev

Required environment variables:

| Variable | Example | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://notification_app:pw@localhost/lotsman` | Async SQLAlchemy DSN |
| `INTERNAL_JWT_SECRET` | `dev-secret-32-chars-minimum` | Shared HS256 key |
| `REDIS_URL` | `redis://localhost:6379/0` | ARQ workers + Streams consumer |
| `SMTP_HOST` | `localhost` | Dev: use Mailpit on port 1025 |
| `SMTP_PORT` | `1025` | |

Run standalone (Postgres, Redis, and Mailpit must be up):

```bash
cd services/notification-service
DATABASE_URL=postgresql+asyncpg://notification_app:pw@localhost/lotsman \
INTERNAL_JWT_SECRET=dev-secret \
SMTP_HOST=localhost SMTP_PORT=1025 \
uv run uvicorn notification_service.main:app --reload --port 8003
```

Or via Docker Compose:

```bash
docker compose -f infra/compose.dev.yml up notification-svc --build
```

Port mapping: `127.0.0.1:8003 → container:8000`. Dev email is captured by Mailpit at http://localhost:8025.

---

## Tests

```bash
uv run pytest services/notification-service/tests -q
```

---

## Directory layout

```
services/notification-service/
├── alembic/
│   └── versions/
├── alembic.ini
├── src/notification_service/
│   ├── domain/             DeliveryRule, DeliveryAttempt entities and errors
│   ├── application/        Use cases, scheduler logic, port protocols
│   ├── infrastructure/
│   │   ├── db/             SQLAlchemy models, session factory
│   │   └── outbox/         Outbox dispatcher ARQ worker
│   ├── api/
│   │   └── v1/             FastAPI routers (added per feature)
│   ├── config.py
│   └── main.py
├── tests/
│   └── unit/
├── Dockerfile
└── pyproject.toml
```

---

## Migrations

```bash
cd services/notification-service
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

The `alembic_version` table lives in the `notification` schema.

---

*Last updated: 2026-06-03*
