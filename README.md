# Лоцман

[![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg)](LICENSE)
![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)
![React 19](https://img.shields.io/badge/React-19-61dafb.svg)
![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-336791.svg)

> **Лоцман** — self-hosted document registry with automated deadline reminders.
> Реестр документов по контрагентам со сроками актуализации и автоматическими
> напоминаниями по Email, Telegram и Dion.

**RU.** Лоцман заменяет таблицу-реестр в Excel: хранит документы по контрагентам
(договоры, лицензии, аудиторские отчёты), отслеживает сроки актуализации и сам
напоминает ответственным — за N дней до срока, в день срока и повторно при
просрочке. Источник правды — PostgreSQL. Интерфейс воспроизводит привычный
Excel-вид и поддерживает экспорт в `.xlsx`.

**EN.** Лоцман replaces a hand-maintained Excel registry. It stores
partner-company documents, tracks renewal deadlines, and delivers
pre-notice / in-day / overdue reminders over Email, Telegram, and Dion.
PostgreSQL is the source of truth; the UI mirrors an Excel grid and exports
`.xlsx`. It is designed to be **self-hosted on a private network** behind your
own reverse proxy.

---

## Features

- 📋 **Excel-like registry** — virtualized data grid (sticky headers, colour-coded
  deadline markers, inline editing, per-column and global server-side search).
- ⏰ **Deadline reminders** — configurable pre-notice / in-day / overdue cadence
  per document type, delivered to the responsible users and to all subscribers.
- 📨 **Multi-channel delivery** — Email (SMTP/EWS), Telegram Bot API, Dion.
- 🗓️ **Calendar integration** — optional Exchange/EWS shared-calendar sync and an
  ICS feed.
- 🔐 **Strong auth** — argon2id passwords, mandatory TOTP (2FA) for every role,
  short-lived RS256 access tokens + opaque refresh cookies, RBAC
  (`admin` / `editor` / `viewer`).
- 🧾 **Full audit trail** — every change is recorded in an append-only audit log
  via a transactional outbox.
- 📤 **Export** — `.xlsx` export of the current (filtered) view.

---

## Quickstart (5 minutes)

Prerequisites: **Docker 26+** with Compose v2, **uv**, **pnpm**.

```bash
git clone https://github.com/MaximilianKaufmannCode/lotsman.git
cd lotsman
cp .env.example .env
# Fill in the required values in .env (every variable is documented inline)
make dev
```

`make dev` builds the images, starts every container, applies all Alembic
migrations, and seeds minimal demo data. When it finishes, open:

| URL | What |
|---|---|
| http://localhost:5173 | SPA (Vite dev server with HMR) |
| http://localhost:8000 | web-bff API |
| http://localhost:8025 | Mailpit (catches all dev email) |

Without `make`, the same steps explicitly:

```bash
docker compose -f infra/compose.dev.yml up -d --build
for s in auth registry notification audit; do
  docker compose -f infra/compose.dev.yml run --rm ${s}-svc alembic upgrade head
done
docker compose -f infra/compose.dev.yml run --rm registry-svc python -m registry_service.scripts.seed
```

For production deployment (Nginx + TLS, generated secrets, `compose.prod.yml`),
see **[docs/deployment/](docs/deployment/README.md)**.

---

## Architecture at a glance

- **Star topology** — `web-bff` is the single fan-out point; backend services
  never call each other over HTTP. See [ADR-0002](docs/adr/0002-service-boundaries.md).
- **Clean architecture** inside every service: `domain/` → `application/` →
  `infrastructure/` + `api/`. Dependencies point inward only (enforced by
  `import-linter`).
- **Async everywhere** — FastAPI + SQLAlchemy 2 (async) + ARQ workers on Redis.
- **Transactional outbox** — every state-changing write also inserts a row into
  `<schema>.outbox`; an ARQ worker publishes it to Redis Streams.
- **Audit as a terminal sink** — `audit-service` consumes every stream and writes
  append-only, month-partitioned records.
- **Internal JWTs** authenticate service-to-service calls (short TTL, audience
  scoped per target service); the BFF is the sole MFA/authorization chokepoint.

---

## Repository layout

```
lotsman/
├── services/                 Python microservices (one package per service)
│   ├── auth-service/         Users, TOTP, JWT, RBAC
│   ├── registry-service/     Documents, attachments, types, xlsx export
│   ├── notification-service/ Delivery rules, scheduling, Email/Telegram/Dion, calendar
│   ├── audit-service/        Append-only event log, read-only API
│   ├── system-control/       Privileged operations sidecar (no database)
│   └── web-bff/              Gateway: external-JWT validation, fan-out, sessions, MFA chokepoint
├── shared/                   Shared kernel package (lotsman-shared)
├── web/                      React 19 + TypeScript SPA
├── infra/                    Docker Compose, Nginx, Postgres init, Prometheus/Grafana/Loki
├── docs/                     Architecture, ADRs, API, DB, user guide, deployment
└── .github/                  CI & security workflows, issue/PR templates
```

---

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12 · FastAPI · SQLAlchemy 2 (async) · Alembic · Pydantic v2 |
| Queue / scheduler | ARQ on Redis 7 |
| Frontend | React 19 · TypeScript (strict) · Vite · TanStack Router/Query/Table/Virtual · Tailwind 4 · shadcn/ui |
| Database | PostgreSQL 16 |
| Auth | argon2id · pyotp (TOTP) · JWT RS256 (15 min access) · opaque refresh (HttpOnly cookie, 7 d) |
| Observability | Prometheus · Grafana · Loki |
| Tooling | `uv` (Python) · `pnpm` (Node) · ruff · mypy · biome · pytest · vitest · Playwright |

See [docs/adr/0001-tech-stack.md](docs/adr/0001-tech-stack.md) for the rationale.

---

## Common tasks

| Target | Description |
|---|---|
| `make dev` | Start the full dev stack with hot reload, then migrate + seed |
| `make down` | Stop all running stacks |
| `make migrate` | Apply Alembic migrations for all four database-backed services |
| `make seed` | Insert minimal demo data |
| `make obs-up` / `make obs-down` | Start / stop the observability overlay |
| `make lint` / `make typecheck` / `make test` | Quality checks (Python + JS/TS) |
| `make ci-local` | Run everything CI runs (lint + typecheck + test) |
| `make build` | Build all production Docker images |
| `make help` | Show the full target list |

---

## Local development

Run a single backend service:

```bash
cd services/auth-service
uv run uvicorn auth_service.main:app --reload --port 8001
# registry → 8002, notification → 8003, audit → 8004, web-bff → 8000
```

Run only the frontend (proxies `/api` to `http://localhost:8000`):

```bash
cd web && pnpm install && pnpm dev   # http://localhost:5173
```

Each service documents its required environment variables in its own `README.md`.

---

## Documentation

| Document | Path |
|---|---|
| Architecture overview | [docs/architecture/README.md](docs/architecture/README.md) |
| Architecture Decision Records | [docs/adr/README.md](docs/adr/README.md) |
| API reference | [docs/api/](docs/api/) |
| Database & migrations | [docs/db/README.md](docs/db/README.md) |
| Deployment guide | [docs/deployment/README.md](docs/deployment/README.md) |
| User guide (RU) | [docs/user-guide/](docs/user-guide/) |
| Security policy | [SECURITY.md](SECURITY.md) |
| Contributing | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Changelog | [CHANGELOG.md](CHANGELOG.md) |

---

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md). Please report security issues privately
per [SECURITY.md](SECURITY.md).

## License

Licensed under the **Mozilla Public License 2.0** — see [LICENSE](LICENSE).
MPL-2.0 is a file-level copyleft license: you may use Лоцман in larger works
(including commercial and proprietary ones), but modifications to MPL-licensed
files must remain under the MPL.
