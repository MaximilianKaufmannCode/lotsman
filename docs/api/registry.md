# API Reference: Registry

This document covers the registry API surface exposed to the SPA through `web-bff`.

- **Base path (SPA-facing):** `https://<host>/api/v1/`
- **Internal base path (direct to registry-service):** `http://registry-svc:8000/api/v1/`
- **OpenAPI UI:** `http://localhost:8002/api/docs` (registry-service) · `http://localhost:8000/api/docs` (web-bff)
- **Service boundaries:** [ADR-0002](../adr/0002-service-boundaries.md)

---

## Overview

```
Browser ──Bearer JWT──> web-bff ──internal JWT (HS256, 60 s, aud=registry-service)──> registry-service
                                                                                              │
                                                                                    registry.outbox
                                                                                              │
                                                                                    ARQ dispatcher
                                                                                              │
                                                                                    Redis Streams
                                                                     ┌────────────────────────┤
                                                                     ▼                        ▼
                                                               audit-service       notification-service
```

`web-bff` is the only entry point for the SPA. It:

1. Validates the external RS256 access JWT (15 min TTL).
2. Enforces role gates before forwarding (fast 403 — no downstream call made).
3. Mints a short-lived internal HS256 JWT (`INTERNAL_JWT_KEY_REGISTRY`, 60 s) and forwards the request to `registry-service`.
4. Passes the response body and status code through unchanged.

`registry-service` re-validates the internal JWT on every request. It never accepts external JWTs directly.

Every state-changing operation writes the business row **and** an outbox row in the same database transaction. The ARQ outbox dispatcher publishes the event to Redis Streams asynchronously. If the dispatcher has not yet run, history panels may show no events for recently created records — this is expected and resolves within seconds.

---

## Authentication

All registry endpoints require a valid access JWT in the `Authorization` header.

```
Authorization: Bearer <access_jwt>
```

The access JWT is issued by `auth-service` on successful login + TOTP. It is RS256-signed, has a 15 min TTL, and carries `sub` (user UUID) and `role` (`admin` | `editor` | `viewer`) claims.

Tokens are stored in-memory by the SPA (`AuthProvider`). On expiry, the SPA silently refreshes via `POST /api/v1/auth/refresh` (HttpOnly cookie). A 401 from any registry endpoint triggers the global 401 interceptor, which performs a refresh and retries.

### Role matrix

| Operation | viewer | editor | admin |
|---|---|---|---|
| List / get documents, assets, document types, exports | yes | yes | yes |
| Create / patch document | no | yes | yes |
| Archive document (single or bulk) | no | yes | yes |
| Restore document | no | no | yes |
| Upload / delete attachment | no | yes | yes |
| Download attachment | yes | yes | yes |
| Request / poll / download export | yes | yes | yes |
| Get document or asset history | yes | yes | yes |
| Create / patch / archive asset | no | no | yes |
| Create / patch document type | no | no | yes |

---

## Pagination

`GET` list endpoints accept `offset` (default 0) and `limit` (default 100, max 1000) as query parameters.

```
GET /api/v1/documents?offset=0&limit=100
```

The response is a plain JSON array (not a paginated envelope). The BFF default for `GET /assets` uses `limit=200`.

---

## Common error shapes

All errors follow FastAPI's default format:

```json
{ "detail": "Human-readable message" }
```

Validation errors return 422 with a list of field-level problems:

```json
{
  "detail": [
    { "loc": ["body", "inn"], "msg": "INN must contain digits only", "type": "value_error" }
  ]
}
```

| Code | Meaning |
|---|---|
| 400 | Malformed request (e.g., bulk archive > 100 rows) |
| 401 | Missing or expired access JWT |
| 403 | Authenticated but insufficient role |
| 404 | Resource not found (or soft-deleted, for PATCH on archived assets) |
| 409 | Conflict (duplicate name, operation invalid for current state) |
| 410 | Export file has been purged (TTL expired) |
| 413 | Attachment exceeds 25 MiB |
| 415 | Attachment MIME type not in allowlist |
| 422 | Request body validation failure |

---

## Documents

### GET /api/v1/documents

List active documents. All query parameters are forwarded verbatim from the BFF to `registry-service`.

**Roles:** viewer, editor, admin

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `q` | string | — | Full-text search across asset name, document number, type display name, notes. Min 2 chars; shorter queries are ignored by the frontend. Uses `pg_trgm` GIN index. |
| `asset_id` | UUID | — | Filter by partner company. |
| `type_code` | string | — | Filter by document type code (e.g., `contract`). |
| `status` | string | — | Filter by computed urgency: `ok` \| `soon` \| `overdue` \| `archived`. |
| `include_archived` | bool | `false` | Include soft-deleted rows. |
| `sort` | string | — | Column name to sort by (e.g., `expiry_date`). Multi-column: comma-separated (e.g., `type_code,expiry_date`). |
| `dir` | string | — | Sort direction: `asc` \| `desc`. Multi-column: comma-separated to match `sort`. |
| `offset` | int | `0` | Pagination offset. |
| `limit` | int | `100` | Page size (max 1000). |

**Response 200** — array of `Document` objects:

```json
[
  {
    "id": "018e4c1a-1234-7abc-8def-000000000001",
    "asset_id": "018e4c1a-0000-7000-8000-000000000001",
    "type_code": "contract",
    "number": "ДГ-2026-99",
    "issue_date": "2026-01-01",
    "expiry_date": "2027-01-01",
    "responsible_user_id": "018e4c1a-0000-7000-8000-000000000042",
    "status": "active",
    "urgency_status": "ok",
    "notes": null,
    "created_by": "018e4c1a-0000-7000-8000-000000000042",
    "updated_by": "018e4c1a-0000-7000-8000-000000000042",
    "created_at": "2026-05-07T09:00:00Z",
    "updated_at": "2026-05-07T09:00:00Z",
    "deleted_at": null
  }
]
```

`urgency_status` is computed server-side at read time from `expiry_date` and `deleted_at` (see [Status computation](#status-computation-algorithm)). `status` carries the raw DB value (`active` | `archived`). The SPA uses `urgency_status` for badge rendering.

**Related stories:** US-1, US-2, US-3

---

### POST /api/v1/documents

Create a new document.

**Roles:** editor, admin

**Request body:**

```json
{
  "asset_id": "018e4c1a-0000-7000-8000-000000000001",
  "type_code": "contract",
  "number": "ДГ-2026-99",
  "issue_date": "2026-01-01",
  "expiry_date": "2027-01-01",
  "responsible_user_id": "018e4c1a-0000-7000-8000-000000000042",
  "notes": "Тестовый договор"
}
```

| Field | Type | Required | Constraints |
|---|---|---|---|
| `asset_id` | UUID | yes | Must reference an active (non-deleted) asset. |
| `type_code` | string | yes | Must match a known `document_types.code`. Max 64 chars. |
| `number` | string | no | Max 500 chars. |
| `issue_date` | date (ISO-8601) | no | — |
| `expiry_date` | date (ISO-8601) | no | If omitted, `urgency_status` = `ok` permanently; no notification schedule created. |
| `responsible_user_id` | UUID | no | Must reference an active user (enforced downstream by orphan-watch). |
| `notes` | string | no | Max 10 000 chars. |

**Response 201** — created `Document` object (same shape as list item).

**Errors:**
- `422` — `asset_id` references a soft-deleted asset (`"Asset not found or archived"`), unknown `type_code`, or `notes` > 10 000 chars.

**Audit event emitted:** `registry.document.created.v1` on Redis Stream `registry.documents`

**Related stories:** US-5

---

### GET /api/v1/documents/{document_id}

Get document detail including attachments.

**Roles:** viewer, editor, admin

**Path parameter:** `document_id` (UUID)

**Response 200:**

```json
{
  "document": { /* Document object */ },
  "attachments": [
    {
      "id": "018e4c1a-0000-7001-8001-000000000001",
      "document_id": "018e4c1a-1234-7abc-8def-000000000001",
      "original_filename": "contract_2026.pdf",
      "mime_type": "application/pdf",
      "size_bytes": 2411724,
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "created_by": "018e4c1a-0000-7000-8000-000000000042",
      "created_at": "2026-05-07T09:00:00Z"
    }
  ]
}
```

**Errors:** `404` — document not found.

**Related stories:** US-8

---

### PATCH /api/v1/documents/{document_id}

Inline-edit a single field of a document.

**Roles:** editor, admin

**Path parameter:** `document_id` (UUID)

**Request body (single-field patch):**

```json
{ "field": "number", "value": "ДГ-2026-100" }
```

`field` must be a patchable field name. `value` is any JSON-serializable value appropriate for the field. Required fields (e.g., `asset_id`) cannot be set to `null`.

**Response 200** — updated `Document` object.

**Errors:**
- `404` — document not found.
- `422` — invalid field name or value fails validation.

**Audit event emitted:** `registry.document.updated.v1` with `field`, `before`, `after`, `actor_id`, `request_id`.

**Related stories:** US-4, US-22

---

### DELETE /api/v1/documents/{document_id}

Soft-delete (archive) a document. Sets `deleted_at` to now.

**Roles:** editor, admin

**Note — SPA/BFF contract gap:** The SPA (`api.ts` `archiveDocument`) calls `PATCH /api/v1/documents/{id}/archive`, but the BFF proxy and registry-service implement archive as `DELETE /api/v1/documents/{id}`. The BFF routes `DELETE /documents/{document_id}` to this endpoint. **Verify with frontend whether `api.ts` uses the correct method or whether the BFF has a `PATCH .../archive` alias.** Flag: **TBD.**

**Response 200:**

```json
{ "detail": "Document archived" }
```

Operation is idempotent: archiving an already-archived document returns 200 without modifying `deleted_at` or emitting a duplicate event.

**Errors:** `403` — viewer role.

**Audit event emitted:** `registry.document.archived.v1`

**Related stories:** US-6

---

### POST /api/v1/documents/{document_id}/restore

Restore a soft-deleted document. Clears `deleted_at` and sets `status` back to `active`.

**Roles:** admin only

**Path parameter:** `document_id` (UUID)

**Response 200:**

```json
{ "detail": "Document restored" }
```

Operation is idempotent: restoring an active document returns 200 without changes or events.

**Errors:** `403` — non-admin role.

**Audit event emitted:** `registry.document.updated.v1` with `field="deleted_at"`, `before=<timestamp>`, `after=null`.

**Related stories:** US-7

---

### POST /api/v1/documents/bulk-archive

Archive up to 100 documents in a single database transaction.

**Roles:** editor, admin

**Request body:**

```json
{ "ids": ["018e4c1a-...", "018e4c1a-..."] }
```

`ids` must contain 1–100 UUIDs. Pydantic enforces `min_length=1, max_length=100`.

**Response 200:**

```json
{ "archived": 12, "skipped": 3 }
```

`archived` — rows whose `deleted_at` was set now. `skipped` — rows that were already archived; they are not modified.

**Errors:**
- `400` — more than 100 IDs submitted (`"Bulk operation limited to 100 rows"`).
- `403` — viewer role.

**Audit events emitted:** one `registry.document.archived.v1` per newly archived document, all within the same transaction.

**Related stories:** US-23

---

## Assets

### GET /api/v1/assets

List active partner companies. Sorted alphabetically by name.

**Roles:** viewer, editor, admin

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `q` | string | — | `pg_trgm` name search (GIN index `assets_name_trgm_idx`). |
| `offset` | int | `0` | Pagination offset. |
| `limit` | int | `200` | Page size (max 1000). |

**Response 200** — array of `Asset` objects:

```json
[
  {
    "id": "018e4c1a-0000-7000-8000-000000000001",
    "name": "ООО Ромашка",
    "inn": "7701234567",
    "notes": null,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
    "deleted_at": null
  }
]
```

Archived assets (`deleted_at IS NOT NULL`) are excluded by default.

**Related stories:** US-12

---

### POST /api/v1/assets

Create a partner company.

**Roles:** admin only

**Request body:**

```json
{ "name": "ООО Новая Компания", "inn": "7701234567", "notes": "" }
```

| Field | Type | Required | Constraints |
|---|---|---|---|
| `name` | string | yes | 1–500 chars. Unique among active assets (partial unique index on `deleted_at IS NULL`). |
| `inn` | string | no | 10 or 12 digits. Digits only. ФНС checksum validated in `inn_policy.py`. |
| `notes` | string | no | — |

**Response 201** — created `Asset` object.

**Errors:**
- `409` — duplicate name among active assets (`"Контрагент с таким названием уже существует"`).
- `422` — invalid INN format.

**Audit event emitted:** `registry.asset.created.v1`

**Related stories:** US-13

---

### PATCH /api/v1/assets/{asset_id}

Update asset name, INN, or notes.

**Roles:** admin only

**Path parameter:** `asset_id` (UUID)

**Request body:** same fields as `POST`, all optional (partial update).

**Response 200** — updated `Asset` object.

**Errors:**
- `404` — asset not found or already archived.
- `422` — invalid INN format.

**Audit event emitted:** `registry.asset.updated.v1` with field-level `before`/`after`.

**Related stories:** US-14

---

### PATCH /api/v1/assets/{asset_id}/archive

Soft-delete an asset and cascade-archive all its active documents.

**Roles:** admin only

**Path parameter:** `asset_id` (UUID)

**Response 200:**

```json
{ "cascaded_document_count": 12 }
```

`cascaded_document_count` is the number of documents whose `deleted_at` was set in this operation. Already-archived documents are skipped (idempotent).

**Errors:** `403` — non-admin.

**Audit events emitted:** `registry.asset.updated.v1` (asset archived) + one `registry.document.archived.v1` per cascaded document, all in the same transaction. The asset event payload includes `cascaded_document_count`.

**Related stories:** US-15

---

### GET /api/v1/assets/{asset_id}/history

Returns the audit event log for an asset. Proxied to `audit-service`.

**Roles:** viewer, editor, admin

**Query parameters:** `limit` (default 50, max 200).

**Response 200** — array of audit event objects (schema defined by `audit-service`; see `docs/api/audit.md` when available).

**Related stories:** US-19

---

## Document Types

### GET /api/v1/document-types

List all document types. Available to all roles; used to populate the type dropdown in the Create Document form.

**Roles:** viewer, editor, admin

**Response 200:**

```json
[
  {
    "code": "contract",
    "display_name": "Договор",
    "pre_notice_days": [30, 14, 7, 1],
    "notify_in_day": true,
    "overdue_every_days": 7,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z"
  }
]
```

`pre_notice_days` — sorted list of positive integers indicating how many calendar days before `expiry_date` a pre-notice notification is sent.

**Related stories:** US-16

---

### POST /api/v1/document-types

Create a new document type.

**Roles:** admin only

**Request body:**

```json
{
  "code": "nda",
  "display_name": "Соглашение о неразглашении",
  "pre_notice_days": [30, 7],
  "notify_in_day": true,
  "overdue_every_days": 7
}
```

| Field | Type | Required | Constraints |
|---|---|---|---|
| `code` | string | yes | Pattern `^[a-z][a-z0-9_]{0,63}$`. Immutable after creation (PATCH ignores it). |
| `display_name` | string | yes | 1–200 chars. |
| `pre_notice_days` | int[] | yes | Min 1 element. All values must be positive integers. |
| `notify_in_day` | bool | no | Default `true`. |
| `overdue_every_days` | int | yes | Minimum 1. |

**Response 201** — created `DocumentType` object.

**Errors:**
- `422` — `code` fails regex, `pre_notice_days` contains non-positive values, `overdue_every_days` = 0.

**Audit event emitted:** `registry.document_type.upserted.v1`

**Related stories:** US-17

---

### PATCH /api/v1/document-types/{code}

Update an existing document type's notification configuration. The `code` in the URL takes precedence; any `code` in the request body is ignored.

**Roles:** admin only

**Path parameter:** `code` (string, e.g., `contract`)

**Request body:** same shape as POST (all fields are re-applied; this is a full-replace of the notification config, not a partial patch).

**Response 200** — updated `DocumentType` object.

**Errors:** `404` — code not found.

**Audit event emitted:** `registry.document_type.upserted.v1`

**Related stories:** US-17

---

## Attachments

### POST /api/v1/documents/{document_id}/attachments

Upload a file attachment to a document. Accepts `multipart/form-data` with a single `file` field.

**Roles:** editor, admin

**Path parameter:** `document_id` (UUID)

**Request:** `multipart/form-data`

```
Content-Type: multipart/form-data; boundary=...

--boundary
Content-Disposition: form-data; name="file"; filename="contract_2026.pdf"
Content-Type: application/pdf

<binary file data>
--boundary--
```

The BFF validates the Content-Length before forwarding (defense-in-depth). `registry-service` re-reads the bytes and enforces the limit a second time.

**Size limit:** 25 MiB. Requests exceeding this return `413` immediately — no bytes are written to disk.

**MIME allowlist** (sniffed from the first bytes, not extension):

| Display name | MIME type |
|---|---|
| PDF | `application/pdf` |
| JPEG | `image/jpeg` |
| PNG | `image/png` |
| TIFF | `image/tiff` |
| DOCX | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| XLSX | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |

**Response 201:**

```json
{
  "id": "018e4c1a-0000-7001-8001-000000000001",
  "document_id": "018e4c1a-1234-7abc-8def-000000000001",
  "original_filename": "contract_2026.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 2411724,
  "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "created_by": "018e4c1a-0000-7000-8000-000000000042",
  "created_at": "2026-05-07T09:00:00Z"
}
```

**Errors:**
- `409` — document is archived (`"Нельзя добавить вложение к архивному документу"`).
- `413` — file exceeds 25 MiB.
- `415` — MIME type not in allowlist.

**Audit event emitted:** `registry.document.updated.v1` with `actor_id` of the uploader.

**Note — SPA contract gap:** `api.ts` calls `GET /api/v1/documents/{documentId}/attachments` (listAttachments), but neither the BFF nor `registry-service` exposes a dedicated `GET /documents/{id}/attachments` list endpoint — the attachment list is returned inline in `GET /documents/{id}`. **TBD: verify with frontend whether `listAttachments` is used or if it has been superseded by the detail endpoint.**

**Related stories:** US-9

---

### GET /api/v1/attachments/{attachment_id}/download

Generate a signed download URL and redirect to it.

**Roles:** viewer, editor, admin

**Path parameter:** `attachment_id` (UUID)

**Response 302** — redirect to signed URL.

The signed URL is `HMAC-SHA256(attachment_id + expires_at)` with a TTL of 60 seconds from generation. The URL is served by Nginx which validates the signature and TTL before streaming the file. The file is served with `Content-Disposition: attachment; filename="<original_filename>"`.

The SPA follows the redirect automatically (`redirect: "follow"`) and uses `res.url` as the download URL.

**Errors:**
- `404` — attachment not found.

**Related stories:** US-10

---

### DELETE /api/v1/attachments/{attachment_id}

Hard-delete an attachment. The file is removed from disk; the `registry.attachments` row is deleted. There is no soft-delete for attachments.

**Roles:** editor, admin

**Path parameter:** `attachment_id` (UUID)

**Response 204** — no body.

**Errors:**
- `404` — attachment not found (also returned for second delete — no idempotent 204).
- `409` — attachment belongs to an archived document (`"Нельзя удалить вложение из архивного документа"`).

**Audit event emitted:** `registry.document.updated.v1` with `actor_id`.

**Related stories:** US-11

---

## Exports

### POST /api/v1/exports

Request an asynchronous `.xlsx` export. The export captures a snapshot of the registry **at job start time** (not when the POST was received, but when the ARQ worker begins executing). This is per acceptance decision Q2 in requirements/registry-crud.md.

**Roles:** viewer, editor, admin

**Note — SPA contract gap:** `api.ts` sends the POST to `/api/v1/exports/xlsx`, but the BFF and registry-service expose the endpoint at `POST /api/v1/exports` (no `/xlsx` suffix). **TBD: verify with frontend and backend which path is canonical.**

**Request body:**

```json
{
  "filters": {
    "q": "Газпром",
    "type_code": "contract",
    "status": "soon"
  },
  "visible_columns": ["asset_name", "type", "number", "expiry_date", "status"]
}
```

`filters` mirrors the query parameters accepted by `GET /documents`. `visible_columns` determines which columns appear in the generated spreadsheet.

**Response 202:**

```json
{ "job_id": "018e4c1a-0000-7002-8002-000000000001", "status": "pending" }
```

After response, the BFF attempts to enqueue an ARQ task `run_export_job`. If the ARQ pool is unavailable, the job row exists in the database and the worker picks it up on its next poll.

**Related stories:** US-20

---

### GET /api/v1/exports/{job_id}

Poll export job status.

**Roles:** viewer, editor, admin

**Path parameter:** `job_id` (UUID)

**Response 200:**

```json
{
  "job_id": "018e4c1a-0000-7002-8002-000000000001",
  "status": "done",
  "file_path": null,
  "error": null,
  "expires_at": "2026-05-08T09:00:00Z",
  "created_at": "2026-05-07T09:00:00Z",
  "updated_at": "2026-05-07T09:00:30Z"
}
```

`status` lifecycle: `pending` → `running` → `done` | `failed`. `file_path` is an internal server path — not exposed to the SPA; use the `/download` endpoint instead. `expires_at` is `completed_at + 24h`. After 24 h the file is purged by the `purge_expired_exports` ARQ cron (runs hourly).

**Errors:** `404` — job not found.

---

### GET /api/v1/exports/{job_id}/download

Download a completed export file.

**Roles:** viewer, editor, admin

**Path parameter:** `job_id` (UUID)

**Response 302** — redirect to signed URL for the generated `.xlsx` file. Filename: `Лоцман_реестр_YYYY-MM-DD.xlsx`.

**Errors:**
- `404` — job not found.
- `410` — file has been purged (`"Файл экспорта истёк. Создайте новый экспорт."`). Create a new export.

---

## History

History endpoints proxy to `audit-service`. They accept the actor's JWT and forward it with a service-scoped internal JWT.

### GET /api/v1/documents/{document_id}/history

Return the audit event log for a document in descending `occurred_at` order.

**Roles:** viewer, editor, admin

**Query parameters:** `limit` (default 50, max 200).

**Response 200** — array of audit event objects. Each event includes:

```json
{
  "id": "...",
  "entity_type": "document",
  "entity_id": "018e4c1a-1234-7abc-8def-000000000001",
  "event_type": "updated",
  "field": "number",
  "before": "ДГ-2026-99",
  "after": "ДГ-2026-100",
  "actor_id": "018e4c1a-0000-7000-8000-000000000042",
  "request_id": "c1a2b3d4-...",
  "occurred_at": "2026-05-07T10:00:00Z"
}
```

Actor name resolution (display name from `actor_id`) is performed by the BFF or frontend, not by `audit-service`.

If `audit-service` returns 503, the BFF propagates the error. The SPA shows «История изменений временно недоступна» with a retry button.

**Related stories:** US-18

---

### GET /api/v1/assets/{asset_id}/history

Return the audit event log for an asset. Same shape and parameters as the document history endpoint.

**Roles:** viewer, editor, admin

**Related stories:** US-19

---

## Search Semantics

Search uses PostgreSQL's `pg_trgm` extension via GIN indexes:

| Index | Table | Column |
|---|---|---|
| `assets_name_trgm_idx` | `registry.assets` | `name` |
| `documents_number_trgm_idx` | `registry.documents` | `number` |

Queries shorter than 2 characters bypass the server (client-side guard in the SPA). The `pg_trgm` similarity threshold is the PostgreSQL default (0.3). Queries that fall below the threshold fall back to `ILIKE '%query%'`. Results are ranked by similarity score descending.

p95 latency target: < 300 ms over 100 000 rows on the production Postgres instance (NFR §6).

---

## Status Computation Algorithm

`urgency_status` is computed at read time by `registry-service` and never stored in the database. The algorithm (mirrored in `web/src/features/registry/computeStatus.ts`):

```
today = current calendar date (UTC)

if deleted_at IS NOT NULL:
    return "archived"
elif expiry_date IS NULL:
    return "ok"
elif expiry_date < today:
    return "overdue"
elif (expiry_date - today) <= 30 days:
    return "soon"
else:
    return "ok"
```

Boundary: a document with `expiry_date = today` returns `"soon"` (0 days remaining, not overdue). Calendar days, no holiday/weekend shift (acceptance decision Q4).

---

## Environment Variables

### registry-service

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | yes | Async SQLAlchemy DSN: `postgresql+asyncpg://registry_app:<pw>@<host>/lotsman` |
| `REDIS_URL` | yes | ARQ broker and outbox dispatcher: `redis://<host>:6379/0` |
| `INTERNAL_JWT_KEY_REGISTRY` | yes | HS256 key for validating internal JWTs from `web-bff`. Min 32 chars. |
| `INTERNAL_JWT_KEY_AUDIT` | yes | HS256 key for minting internal JWTs when calling `audit-service`. |
| `ATTACHMENTS_VOLUME_ROOT` | yes | Absolute path to the attachment storage volume (outside nginx web root). |
| `EXPORTS_VOLUME_ROOT` | yes | Absolute path to the export file storage volume. |
| `ATTACHMENT_SIGNED_URL_SECRET` | yes | HMAC-SHA256 key for generating attachment signed URLs. |

### web-bff (registry-related)

| Variable | Required | Description |
|---|---|---|
| `INTERNAL_JWT_KEY_REGISTRY` | yes | HS256 key for minting internal JWTs sent to `registry-service`. Must match registry-service's `INTERNAL_JWT_KEY_REGISTRY`. |
| `REGISTRY_SVC_URL` | yes | Base URL of registry-service (e.g., `http://registry-svc:8000`). |

---

_Last updated: 2026-05-07_
