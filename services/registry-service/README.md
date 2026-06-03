# registry-service

Owns everything users think of as "the registry": partner companies (assets), the documents associated with them, document types that define deadline and notification rules, attachment metadata, and `.xlsx` export jobs.

This is the primary write path: most user actions go `web-bff → registry-service`. Every write produces a domain event via the transactional outbox, which triggers audit recording and notification scheduling downstream.

---

## Owns

**Postgres schema**: `registry` (app role: `registry_app`)

**Domain entities**: `Asset` (партнёрская компания), `DocumentType`, `Document`, `AttachmentMetadata`, `ExportJob`

---

## Public surface

### System

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Liveness — returns `{"status": "ok"}` |
| GET | `/readyz` | Readiness — checks Postgres reachability |
| GET | `/metrics` | Prometheus metrics (text format) |

### Business API (`/api/v1/`)

Consumed exclusively via `web-bff`. See [docs/api/registry.md](../../docs/api/registry.md) for full request/response shapes, error codes, and audit events.

**Documents**

| Method | Path | Min role | Description |
|---|---|---|---|
| GET | `/api/v1/documents` | viewer | List with filter + sort + pagination (US-1, US-2, US-3) |
| POST | `/api/v1/documents` | editor | Create document (US-5) |
| GET | `/api/v1/documents/{id}` | viewer | Detail + attachments (US-8) |
| PATCH | `/api/v1/documents/{id}` | editor | Inline-edit single field (US-4) |
| DELETE | `/api/v1/documents/{id}` | editor | Soft-delete / archive (US-6) |
| POST | `/api/v1/documents/{id}/restore` | admin | Restore from archive (US-7) |
| POST | `/api/v1/documents/bulk-archive` | editor | Bulk archive ≤ 100 rows (US-23) |
| GET | `/api/v1/documents/{id}/history` | viewer | Audit history proxy (US-18) |

**Assets**

| Method | Path | Min role | Description |
|---|---|---|---|
| GET | `/api/v1/assets` | viewer | List active assets (US-12) |
| POST | `/api/v1/assets` | admin | Create asset (US-13) |
| PATCH | `/api/v1/assets/{id}` | admin | Update name/INN/notes (US-14) |
| PATCH | `/api/v1/assets/{id}/archive` | admin | Soft-delete + cascade-archive documents (US-15) |
| GET | `/api/v1/assets/{id}/history` | viewer | Audit history proxy (US-19) |

**Document Types**

| Method | Path | Min role | Description |
|---|---|---|---|
| GET | `/api/v1/document-types` | viewer | List all types (US-16) |
| POST | `/api/v1/document-types` | admin | Create type (US-17) |
| PATCH | `/api/v1/document-types/{code}` | admin | Update notification config (US-17) |

**Attachments**

| Method | Path | Min role | Description |
|---|---|---|---|
| POST | `/api/v1/documents/{id}/attachments` | editor | Upload file (multipart, 25 MiB cap, MIME sniff) (US-9) |
| GET | `/api/v1/attachments/{id}/download` | viewer | Signed-URL redirect, TTL 60 s (US-10) |
| DELETE | `/api/v1/attachments/{id}` | editor | Hard delete (US-11) |

**Exports**

| Method | Path | Min role | Description |
|---|---|---|---|
| POST | `/api/v1/exports` | viewer | Request async `.xlsx` export (US-20) |
| GET | `/api/v1/exports/{id}` | viewer | Poll job status (US-20) |
| GET | `/api/v1/exports/{id}/download` | viewer | Signed-URL redirect (410 if purged) (US-20) |

OpenAPI: http://localhost:8002/api/docs (when running locally).

---

## Events published

Published to Redis Stream `registry.documents`:
- `registry.document.created.v1`
- `registry.document.updated.v1`
- `registry.document.archived.v1`
- `registry.document.deleted.v1`

Published to Redis Stream `registry.assets`:
- `registry.asset.created.v1`
- `registry.asset.updated.v1`

Published to Redis Stream `registry.document_types`:
- `registry.document_type.upserted.v1`

## Events consumed

From Redis Stream `auth.users` (consumer group `registry-orphan-watch`):
- `auth.user.deactivated.v1` — flags documents where `responsible_user_id` matches the deactivated user

All events use the canonical envelope from `lotsman_shared.envelope`. See [ADR-0002 §A](../../docs/adr/0002-service-boundaries.md).

---

## Configuration

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | yes | Async SQLAlchemy DSN: `postgresql+asyncpg://registry_app:<pw>@<host>/lotsman` |
| `REDIS_URL` | yes | ARQ broker and outbox dispatcher: `redis://<host>:6379/0` |
| `INTERNAL_JWT_KEY_REGISTRY` | yes | HS256 key for validating internal JWTs minted by `web-bff`. Min 32 chars. Must match `web-bff`'s `INTERNAL_JWT_KEY_REGISTRY`. |
| `INTERNAL_JWT_KEY_AUDIT` | yes | HS256 key for minting internal JWTs when calling `audit-service`. Min 32 chars. |
| `ATTACHMENTS_VOLUME_ROOT` | yes | Absolute path to the attachment storage volume (must be outside nginx web root). Example: `/vol/attachments` |
| `EXPORTS_VOLUME_ROOT` | yes | Absolute path to the export file storage volume. Example: `/vol/exports` |
| `ATTACHMENT_SIGNED_URL_SECRET` | yes | HMAC-SHA256 key used to sign attachment download URLs (TTL 60 s). Inject via Docker secrets in production. |

---

## Local dev

Run standalone (Postgres and Redis must be up; see [Configuration](#configuration) for all variables):

```bash
cd services/registry-service
DATABASE_URL=postgresql+asyncpg://registry_app:pw@localhost/lotsman \
REDIS_URL=redis://localhost:6379/0 \
INTERNAL_JWT_KEY_REGISTRY=dev00000000000000000000000000002 \
INTERNAL_JWT_KEY_AUDIT=dev00000000000000000000000000004 \
ATTACHMENTS_VOLUME_ROOT=/tmp/lotsman-attachments \
EXPORTS_VOLUME_ROOT=/tmp/lotsman-exports \
ATTACHMENT_SIGNED_URL_SECRET=dev-signed-url-secret-32-chars-min \
uv run uvicorn registry_service.main:app --reload --port 8002
```

Or via Docker Compose:

```bash
docker compose -f infra/compose.dev.yml up registry-svc --build
```

Port mapping: `127.0.0.1:8002 → container:8000`.

---

## Tests

```bash
# From repo root:
uv run pytest services/registry-service/tests -q
```

---

## Directory layout

```
services/registry-service/
├── alembic/
│   └── versions/
├── alembic.ini
├── src/registry_service/
│   ├── domain/             Asset, Document, DocumentType entities and errors
│   ├── application/        Use cases (create_document, archive_document, …), ports
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
cd services/registry-service
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

The `alembic_version` table lives in the `registry` schema.

---

*Last updated: 2026-05-07 — updated for the registry-crud feature (real endpoints, configuration, env vars)*
