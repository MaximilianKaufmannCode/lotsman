# registry-service

Owns everything users think of as "the registry": companies, the documents attached to them, document types that define deadline and notification rules, attachment metadata, and `.xlsx` import/export jobs.

> **Terminology:** the user-facing term is **Компания** (Company). The internal identifier in code, the DB schema, and event payloads is `asset` — unchanged since 2.2.0. Read every `asset` below as "company".

This is the primary write path: most user actions go `web-bff → registry-service`. Every write produces a domain event via the transactional outbox, which drives audit recording and notification scheduling downstream.

---

## Owns

**Postgres schema**: `registry` (app role: `registry_app`)

**Domain entities**: `Asset` (Компания), `DocumentType`, `Document`, `AttachmentMetadata`, `ExportJob`

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
| GET | `/api/v1/documents/distinct-values` | viewer | Distinct column values for filter autocomplete; Redis-cached (US-3) |
| POST | `/api/v1/documents` | editor | Create document (US-5) |
| GET | `/api/v1/documents/{id}` | viewer | Detail + attachments (US-8) |
| PATCH | `/api/v1/documents/{id}` | editor | Inline-edit single field (US-4) |
| DELETE | `/api/v1/documents/{id}` | editor | Soft-delete / archive (US-6) |
| POST | `/api/v1/documents/{id}/restore` | admin | Restore from archive (US-7) |
| POST | `/api/v1/documents/bulk-archive` | editor | Bulk archive ≤ 100 rows (US-23) |
| GET | `/api/v1/documents/{id}/history` | viewer | Audit history proxy (US-18) |

**Assets (Компании)**

A company's **ИНН is optional** (2.2.1). When supplied, it is validated as a 10- or 12-digit Russian INN with the ФНС check-digit algorithm; an invalid checksum is rejected.

| Method | Path | Min role | Description |
|---|---|---|---|
| GET | `/api/v1/assets` | viewer | List active companies, optional `pg_trgm` name search (US-12) |
| POST | `/api/v1/assets` | **editor** | Create company — supports inline creation from the document form (issue #5, 2.3.0) |
| PATCH | `/api/v1/assets/{id}` | admin | Update name / INN / notes (US-14) |
| PATCH | `/api/v1/assets/{id}/status` | admin | Set status `active` / `liquidating` / `archived`; archiving sets `deleted_at` and cascade-archives the company's documents |
| PATCH | `/api/v1/assets/{id}/archive` | admin | Soft-delete + cascade-archive documents (US-15) |
| GET | `/api/v1/assets/{id}/history` | viewer | Audit history proxy (US-19) |

**Creating** a company is editor-or-admin so an editor can add one inline while filling out a document form. **Editing, status changes, and archiving stay admin-only.** Status uses a dual signal — the functional `status` enum plus `deleted_at` — and `archived` is the only state that sets `deleted_at`. Un-archiving does **not** auto-restore the company's documents; restore them individually.

**Document Types**

Creating and editing a document type — including its deadline rules, notification schedule, and custom-field schema — is **admin-only**.

| Method | Path | Min role | Description |
|---|---|---|---|
| GET | `/api/v1/document-types` | viewer | List all types (US-16) |
| POST | `/api/v1/document-types` | admin | Create type (US-17) |
| PATCH | `/api/v1/document-types/{code}` | admin | Update notification config (US-17) |
| GET | `/api/v1/document-types/admin/{code}/custom-fields` | admin | Read the custom-field schema |
| PUT | `/api/v1/document-types/admin/{code}/custom-fields` | admin | Replace the custom-field schema (re-MFA enforced at the BFF) |

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

**Imports**

Bulk import from a corporate Excel registry. All import endpoints are **admin-only**; the two-step confirm additionally requires re-MFA, which the BFF verifies (TOTP) before forwarding (ADR-0006 §5).

| Method | Path | Min role | Description |
|---|---|---|---|
| POST | `/api/v1/imports/xlsx` | admin | One-shot import; returns a per-row report (25 MiB cap) |
| POST | `/api/v1/admin/import/preview` | admin | Parse file, classify known/unknown columns, open a session |
| POST | `/api/v1/admin/import/confirm` | admin (re-MFA) | Apply column decisions and insert documents |

**Preferences** (tenant-wide UI defaults)

Reads are open to any authenticated user; writes are admin-only.

| Method | Path | Min role | Description |
|---|---|---|---|
| GET | `/api/v1/preferences/column-order` | viewer | Read column order + pinned column |
| PUT | `/api/v1/admin/preferences/column-order` | admin | Set column order; pinned column is forced leftmost |
| GET | `/api/v1/preferences/column-labels` | viewer | Read per-tenant column renames |
| PUT | `/api/v1/admin/preferences/column-labels` | admin | Set per-tenant column labels |

OpenAPI: http://localhost:8002/api/docs (when running locally).

---

## Events published

All events use the canonical envelope from `lotsman_shared.envelope` and ship through the transactional outbox. Topics map to Redis Stream keys per ADR-0002 §C.

| Topic (Redis Stream) | Event types |
|---|---|
| `registry.documents` | `registry.document.created.v1`, `registry.document.updated.v1`, `registry.document.archived.v1`, `registry.document.restored.v1`, `registry.document.bulk_archived.v1` |
| `registry.assets` | `registry.asset.created.v1`, `registry.asset.updated.v1`, `registry.asset.status_changed.v1`, `registry.asset.archived.v1` |
| `registry.document_types` | `registry.document_type.upserted.v1`, `registry.document_type.fields_updated.v1` |
| `registry.exports` | `registry.export.requested.v1`, `registry.export.completed.v1`, `registry.export.failed.v1`, `registry.export.purged.v1` |
| `registry.imports` | `registry.import.preview.v1`, `registry.import.completed.v1` |

Notes:
- Attachment upload/delete are modelled as `registry.document.updated.v1` (`field: "attachments"`), not as separate event types.
- Export lifecycle: `requested` → `completed` / `failed`, then `purged` when the file is reaped (Q8 cron).
- Import lifecycle: `preview` (session opened) → `completed` (confirm applied).
- Tenant preference writes also emit `registry.preferences.column_order_changed.v1` / `column_labels_changed.v1` on the `registry.preferences` topic.

## Events consumed

From Redis Stream `auth.users` (consumer group `registry-orphan-watch`):
- `auth.user.deactivated.v1` — flags documents where `responsible_user_id` matches the deactivated user

See [ADR-0002 §A](../../docs/adr/0002-service-boundaries.md) for the ownership matrix.

---

## Configuration

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | yes | Async SQLAlchemy DSN: `postgresql+asyncpg://registry_app:<pw>@<host>/lotsman` |
| `REDIS_URL` | yes | ARQ broker and outbox dispatcher: `redis://<host>:6379/0` |
| `INTERNAL_JWT_KEY_REGISTRY` | yes | HS256 key for validating internal JWTs minted by `web-bff`. Min 32 chars. Must match `web-bff`'s `INTERNAL_JWT_KEY_REGISTRY`. |
| `INTERNAL_JWT_KEY_AUDIT` | yes | HS256 key for minting internal JWTs when calling `audit-service`. Min 32 chars. |
| `ATTACHMENTS_VOLUME_ROOT` | no | Absolute path to the attachment storage volume (must be outside the nginx web root). Default `/vol/attachments`. |
| `EXPORTS_VOLUME_ROOT` | no | Absolute path to the export file storage volume. Default `/vol/exports`. |
| `SIGNED_URL_KEY` | yes (prod) | HMAC-SHA256 key signing **both** attachment and export download URLs. Inject via Docker secrets in production; the built-in default is insecure. |
| `AUDIT_SVC_URL` | no | Downstream `audit-service` base URL. Default `http://audit-svc:8000`. |
| `OUTBOX_POLL_INTERVAL_SECONDS` | no | Outbox dispatcher poll interval. Default `1.0`. |

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
SIGNED_URL_KEY=dev-signed-url-secret-32-chars-min \
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
│   ├── domain/             Asset, Document, DocumentType entities, value objects, events, errors
│   ├── application/        Use cases, policies (INN, export, bulk), ports, DTOs
│   ├── infrastructure/
│   │   ├── db/             SQLAlchemy repositories, session factory
│   │   ├── outbox/         Outbox dispatcher ARQ worker
│   │   └── storage/        Attachment / export file storage
│   ├── api/
│   │   └── v1/             FastAPI routers (one per feature area)
│   ├── config.py
│   └── main.py
├── tests/
└── pyproject.toml
```

---

## Migrations

Alembic reads its DSN from `REGISTRY_DATABASE_URL` (not `DATABASE_URL`); export it first or the commands abort with `REGISTRY_DATABASE_URL environment variable is not set`.

```bash
cd services/registry-service
export REGISTRY_DATABASE_URL=postgresql+asyncpg://registry_app:pw@localhost/lotsman
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

The `alembic_version` table lives in the `registry` schema.

---

> **Known role mismatch:** `PATCH /api/v1/assets/{id}/status` is gated at editor+admin in `web-bff` but enforced as admin-only (`RequireAdmin`) in `registry-service`. An editor request passes the BFF gate but is rejected (403) downstream. Treat it as admin-only in practice until the two services are reconciled. See [docs/api/registry.md](../../docs/api/registry.md).

*Last updated: 2026-06-25 — POST /assets now editor-or-admin (inline company create, 2.3.0); optional ИНН with ФНС checksum (2.2.1); full event topics, imports/preferences endpoints, and corrected config (SIGNED_URL_KEY, AUDIT_SVC_URL, OUTBOX_POLL_INTERVAL_SECONDS).*
