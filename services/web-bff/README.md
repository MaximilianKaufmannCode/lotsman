# web-bff

The API gateway for the Лоцман SPA. It is the **only** service the browser talks to directly, and the **only** service that fans out to multiple backends in a single request.

**Rule: no business logic, no schema, no outbox.** If you find yourself adding a SQLAlchemy model or a domain rule here, the logic belongs in a backend service instead.

What `web-bff` does:
- Validates the external access JWT (RS256, 15 min, issued by `auth-service`)
- Mints a short-lived internal JWT (HS256, 60 s, `aud` scoped per target service) for every downstream call
- Fans out to downstream services and merges the responses into SPA-friendly view models
- Owns session state in Redis (short-lived keys only — not business data)
- Serves the static SPA bundle in production (via Nginx upstream)
- Enforces CSRF and cookie semantics

It holds typed clients for **five** downstream targets: `auth-service`, `registry-service`, `notification-service`, `audit-service`, and the optional `system-control` sidecar. The sidecar is wired only when `INTERNAL_JWT_KEY_SYSTEM_CONTROL` is set; it is **absent in production** by design (ADR-0009).

---

## Owns

**No Postgres schema.** `web-bff` has no database connection.

**No domain entities.** Only DTOs and view models assembled from downstream responses.

**Redis**: short-lived session keys only (not a stream publisher or consumer).

---

## Public surface

### System

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Liveness — returns `{"status": "ok"}` |
| GET | `/readyz` | Readiness — checks Redis reachability |
| GET | `/metrics` | Prometheus metrics (text format) |
| GET | `/api/v1/system/health` | Aggregated health — see the [system-control proxy](#apiv1system--system-control-proxy-super-admin-facing) section (super-admin gated) |

### `/api/v1/auth/*` — Auth proxy (SPA-facing)

The refresh token is **never** present in JSON response bodies. The BFF strips it and sets/clears the `refresh` HttpOnly cookie on every login, refresh, and logout response.

All state-changing routes require `Authorization: Bearer <access_jwt>`. The refresh endpoint (`POST /api/v1/auth/refresh`) is cookie-only and must not include a Bearer header.

| Method | Path | Auth required | Proxies to auth-service |
|---|---|---|---|
| POST | `/api/v1/auth/login` | — | `POST /auth/login` |
| POST | `/api/v1/auth/totp/verify` | — | `POST /auth/totp/verify` |
| POST | `/api/v1/auth/backup-codes/verify` | — | `POST /auth/totp/verify` (with `backup_code`) |
| POST | `/api/v1/auth/refresh` | Cookie only | `POST /auth/refresh` |
| POST | `/api/v1/auth/logout` | Bearer | `POST /auth/logout` |
| POST | `/api/v1/auth/totp/enroll` | Bearer | `POST /auth/totp/enroll` |
| POST | `/api/v1/auth/totp/enroll/confirm` | Bearer | `POST /auth/totp/enroll/confirm` |
| POST | `/api/v1/auth/backup-codes/regenerate` | Bearer | `POST /auth/backup-codes/regenerate` |
| GET | `/api/v1/auth/sessions/me` | Bearer | `GET /auth/sessions` |
| DELETE | `/api/v1/auth/sessions/{session_id}` | Bearer | `DELETE /auth/sessions/{session_id}` |
| POST | `/api/v1/auth/re-mfa` | Bearer | `POST /auth/mfa-check` |
| POST | `/api/v1/auth/password/change` | Bearer | `POST /auth/change-password` |
| GET | `/api/v1/auth/me` | Bearer | `GET /auth/me` (profile, incl. `ui_font_scale` — 2.1.0) |
| PATCH | `/api/v1/auth/me` | Bearer | `PATCH /auth/me` (update profile / font-scale) |

The `/api/v1/auth/me` proxy also backs the SPA Профиль page: notification matrix, email change, and the **Размер шрифта** preference (`ui_font_scale`, 80–150%, added in 2.1.0). Self-service profile, saved-filters and notification routes under `/api/v1/auth/me/*` are forwarded the same way.

**Refresh cookie attributes** (set on successful login or refresh):

| Attribute | Value |
|---|---|
| `HttpOnly` | yes |
| `Secure` | yes |
| `SameSite` | `Strict` |
| `Path` | `/api/v1/auth` |
| `Max-Age` | `604800` (7 days) |

### `/api/v1/admin/*` — Admin proxy (SPA-facing)

All routes require a Bearer token with `role = "admin"`. Non-admins receive `403` before the upstream is contacted. Write operations additionally require the admin to have passed re-MFA (`POST /api/v1/auth/re-mfa`) in the current session.

| Method | Path | Re-MFA | Proxies to auth-service |
|---|---|---|---|
| GET | `/api/v1/admin/users` | No | `GET /admin/users` |
| GET | `/api/v1/admin/users/{id}` | No | `GET /admin/users/{id}` |
| POST | `/api/v1/admin/users` | Yes | `POST /admin/users` |
| PATCH | `/api/v1/admin/users/{id}` | Yes | `PATCH /admin/users/{id}/role` and/or `POST /admin/users/{id}/deactivate` |
| POST | `/api/v1/admin/users/{id}/lockout` | Yes | `POST /admin/users/{id}/lockout` |
| DELETE | `/api/v1/admin/users/{id}/lockout` | Yes | `DELETE /admin/users/{id}/lockout` |
| GET | `/api/v1/admin/users/{id}/sessions` | No | `GET /admin/users/{id}/sessions` |
| DELETE | `/api/v1/admin/users/{id}/sessions` | Yes | `DELETE /admin/users/{id}/sessions` |
| POST | `/api/v1/admin/users/{id}/totp/reset` | Inline (`admin_totp_code`) | `POST /admin/users/{id}/totp/reset` |
| POST | `/api/v1/admin/users/{id}/password/reset` | Yes | `POST /admin/users/{id}/password/reset` |

### `/api/v1/registry/*` — Registry proxy (SPA-facing)

All routes require a Bearer token. The BFF enforces role gates before forwarding to `registry-service`; `registry-service` re-validates the internal JWT independently. See [docs/api/registry.md](../../docs/api/registry.md) for full endpoint documentation.

> The technical identifiers `asset` / `asset_id` are the stable code/DB name for the user-facing entity **Компания** (renamed from «Контрагент» in 2.2.0). The paths below are unchanged.

| Method | Path | Min role | Proxies to registry-service |
|---|---|---|---|
| GET | `/api/v1/assets` | viewer | `GET /api/v1/assets` |
| POST | `/api/v1/assets` | **editor** | `POST /api/v1/assets` — editor or admin (see note ¹) |
| PATCH | `/api/v1/assets/{id}` | admin | `PATCH /api/v1/assets/{id}` |
| PATCH | `/api/v1/assets/{id}/archive` | admin | `PATCH /api/v1/assets/{id}/archive` |
| PATCH | `/api/v1/assets/{id}/status` | editor | `PATCH /api/v1/assets/{id}/status` (editor or admin; value validated at proxy) |
| GET | `/api/v1/assets/{id}/history` | viewer | `GET /api/v1/assets/{id}/history` |
| GET | `/api/v1/document-types` | viewer | `GET /api/v1/document-types` |
| POST | `/api/v1/document-types` | admin | `POST /api/v1/document-types` (admin only — see note ¹) |
| PATCH | `/api/v1/document-types/{code}` | admin | `PATCH /api/v1/document-types/{code}` |
| GET | `/api/v1/documents` | viewer | `GET /api/v1/documents` (repeated multi-select params preserved) |
| GET | `/api/v1/documents/distinct-values` | viewer | `GET /api/v1/documents/distinct-values` (column-filter autocomplete) |
| POST | `/api/v1/documents` | editor | `POST /api/v1/documents` |
| GET | `/api/v1/documents/{id}` | viewer | `GET /api/v1/documents/{id}` |
| PATCH | `/api/v1/documents/{id}` | editor | `PATCH /api/v1/documents/{id}` |
| DELETE | `/api/v1/documents/{id}` | editor | `DELETE /api/v1/documents/{id}` (archive) |
| POST | `/api/v1/documents/{id}/restore` | admin | `POST /api/v1/documents/{id}/restore` |
| POST | `/api/v1/documents/bulk-archive` | editor | `POST /api/v1/documents/bulk-archive` |
| GET | `/api/v1/documents/{id}/history` | viewer | `GET /api/v1/documents/{id}/history` (actor/responsible UUIDs enriched to ФИО via auth-service) |
| POST | `/api/v1/documents/{id}/attachments` | editor | `POST /api/v1/documents/{id}/attachments` (Content-Length validated at proxy; 25 MiB cap) |
| GET | `/api/v1/documents/{id}/attachments` | viewer | `GET /api/v1/documents/{id}/attachments` (list) |
| GET | `/api/v1/attachments/{id}/download` | viewer | `GET /api/v1/attachments/{id}/download` (302 redirect passthrough) |
| DELETE | `/api/v1/attachments/{id}` | editor | `DELETE /api/v1/attachments/{id}` |
| POST | `/api/v1/exports` | viewer | `POST /api/v1/exports` |
| POST | `/api/v1/exports/xlsx` | viewer | `POST /api/v1/exports` (SPA-compat alias) |
| GET | `/api/v1/exports/{id}` | viewer | `GET /api/v1/exports/{id}` |
| GET | `/api/v1/exports/{id}/download` | viewer | `GET /api/v1/exports/{id}/download` (302 or 410 passthrough) |
| POST | `/api/v1/imports/xlsx` | admin | `POST /api/v1/imports/xlsx` (bulk Excel import; 25 MiB cap) |
| GET | `/api/v1/events` | viewer | `GET /api/v1/audit/events` (audit-service; entity-history bridge) |
| GET | `/api/v1/preferences/column-order` | viewer | `GET` registry preferences |
| PUT | `/api/v1/admin/preferences/column-order` | admin | `PUT` registry preferences |
| GET | `/api/v1/preferences/column-labels` | viewer | `GET` registry preferences |
| PUT | `/api/v1/admin/preferences/column-labels` | admin | `PUT` registry preferences |

¹ **RBAC since 2.3.0 (issue #5):** creating a Компания (`POST /api/v1/assets`) is open to **editor or admin** so the document-creation form can create a company inline. Editing and archiving a Компания stay **admin-only**. Creating a **Тип документа** (`POST /api/v1/document-types`) remains **admin-only**.

### `/api/v1/system/*` — System-control proxy (super-admin-facing)

The platform-administration surface for the super-admin panel (ADR-0006). **Every route requires `role == super_admin`** — any other role gets `403` before an upstream is contacted. Mutating operations additionally require TOTP re-MFA; the destructive ones (`backup-now`, `restart-service`, `migrate`) also require a typed-confirmation string.

This proxy is wired **only when `INTERNAL_JWT_KEY_SYSTEM_CONTROL` is configured**. The `system-control` sidecar is not part of the production compose (ADR-0009); when it is absent, sidecar-backed routes return `503` instead of crashing the gateway.

| Method | Path | Re-MFA | Source |
|---|---|---|---|
| GET | `/api/v1/system/health` | No | Aggregated health — probes the four backends + web-bff self |
| GET | `/api/v1/system/queues` | No | Outbox / stream-lag overview |
| GET | `/api/v1/system/migrations` | No | Latest alembic revision per service |
| GET | `/api/v1/system/keys` | No | Key-rotation records (`auth-service`) |
| POST | `/api/v1/system/keys/{id}/rotated` | Yes | Record a manual key rotation (`auth-service`) |
| GET | `/api/v1/system/logs` | No | Container log tail (sidecar) |
| GET | `/api/v1/system/audit` | No | System-filtered audit log (`audit-service`) |
| POST | `/api/v1/system/backup-now` | Yes + confirm | Trigger a backup (sidecar) |
| POST | `/api/v1/system/restart-service` | Yes + confirm | Restart a service container (sidecar) |
| POST | `/api/v1/system/migrate` | Yes + confirm | Run `alembic upgrade head` for a service (sidecar) |

See ADR-0006 (super-admin role + system panel) and ADR-0009 (system-control privilege reduction) for the privileged-component design and its production posture.

> A legacy unauthenticated `GET /api/v1/system/health` scaffold is still registered for infra probes, but the super-admin-gated route above shadows it for that exact path.

### Security middleware

`InboundHeaderSanitiser` — strips client-supplied internal-trust headers (`X-Internal-Token`, `X-Forwarded-User`, `X-Forwarded-Roles`, `X-Internal-Actor`) before the request is processed. Prevents external callers from injecting internal identity claims. Runs on every request, ahead of all routers. Closes F-008.

OpenAPI: http://localhost:8000/api/docs (when running locally).

---

## Events published / consumed

**None.** `web-bff` communicates with downstream services via synchronous HTTP only (internal JWTs). It does not read or write Redis Streams.

---

## Local dev

Required environment variables:

| Variable | Example | Notes |
|---|---|---|
| `INTERNAL_JWT_KEY_AUTH` | _(32+ random hex chars)_ | HS256 key for calls to `auth-service`. Min 32 chars; startup fails otherwise. |
| `INTERNAL_JWT_KEY_REGISTRY` | _(32+ random hex chars)_ | HS256 key for calls to `registry-service`. |
| `INTERNAL_JWT_KEY_NOTIFICATION` | _(32+ random hex chars)_ | HS256 key for calls to `notification-service`. |
| `INTERNAL_JWT_KEY_AUDIT` | _(32+ random hex chars)_ | HS256 key for calls to `audit-service`. |
| `INTERNAL_JWT_KEY_SYSTEM_CONTROL` | _(optional, 32+ chars)_ | HS256 key for calls to the `system-control` sidecar. **Omit in prod (ADR-0009)** — when unset, sidecar-backed `/api/v1/system/*` routes return `503`. |
| `JWT_PUBLIC_KEY_PATH` | `/run/secrets/jwt_public.pem` | RS256 public key for verifying access tokens issued by `auth-service`. |
| `REDIS_URL` | `redis://localhost:6379/0` | Session store |
| `AUTH_SVC_URL` | `http://localhost:8001` | Overridden by compose to use container DNS |
| `REGISTRY_SVC_URL` | `http://localhost:8002` | |
| `NOTIFICATION_SVC_URL` | `http://localhost:8003` | |
| `AUDIT_SVC_URL` | `http://localhost:8004` | |
| `SYSTEM_CONTROL_URL` | `http://localhost:8005` | Base URL for the sidecar (internal loopback only; used only when the key above is set). |

The former single `INTERNAL_JWT_SECRET` is replaced by per-service keys per [ADR-0003 §10](../../docs/adr/0003-authentication-and-session-lifecycle.md): four mandatory (auth, registry, notification, audit) plus an optional fifth for `system-control`. `web-bff` holds them all; each backend holds only its own. Startup fails fast if any two keys are identical (audience-isolation, F-002).

Run standalone (all backend services must be running):

```bash
cd services/web-bff
INTERNAL_JWT_KEY_AUTH=dev00000000000000000000000000001 \
INTERNAL_JWT_KEY_REGISTRY=dev00000000000000000000000000002 \
INTERNAL_JWT_KEY_NOTIFICATION=dev00000000000000000000000000003 \
INTERNAL_JWT_KEY_AUDIT=dev00000000000000000000000000004 \
JWT_PUBLIC_KEY_PATH=./dev-keys/jwt_public.pem \
AUTH_SVC_URL=http://localhost:8001 \
REGISTRY_SVC_URL=http://localhost:8002 \
NOTIFICATION_SVC_URL=http://localhost:8003 \
AUDIT_SVC_URL=http://localhost:8004 \
REDIS_URL=redis://localhost:6379/0 \
uv run uvicorn web_bff.main:app --reload --port 8000
```

Or via Docker Compose (recommended — starts all dependencies):

```bash
docker compose -f infra/compose.dev.yml up web-bff --build
```

Port mapping: `127.0.0.1:8000 → container:8000`.

---

## Tests

```bash
uv run pytest services/web-bff/tests -q
```

Integration tests use `respx` to mock downstream HTTP calls — no real backend services needed.

---

## Directory layout

```
services/web-bff/
├── src/web_bff/
│   ├── domain/             Empty — intentional. No business entities.
│   ├── application/        Aggregation logic (fan-out + response composition)
│   ├── infrastructure/
│   │   ├── redis/          Session store adapter
│   │   ├── middleware/
│   │   │   └── inbound_header_sanitiser.py   Strips client-injected internal headers (F-008)
│   │   └── clients/        Typed HTTP clients per downstream service
│   │       ├── base.py     DownstreamClient — internal-JWT minting, shared transport
│   │       ├── auth_client.py
│   │       ├── registry_client.py
│   │       ├── notification_client.py
│   │       ├── audit_client.py
│   │       └── system_control_client.py      Sidecar client (optional)
│   ├── api/
│   │   ├── deps.py         FastAPI dependencies (JWT verification, settings)
│   │   └── v1/             Active routers:
│   │       ├── auth.py            /api/v1/auth/* proxy (incl. /me profile)
│   │       ├── admin.py           /api/v1/admin/* proxy
│   │       ├── registry.py        /api/v1/{assets,documents,...} proxy
│   │       ├── system.py          /api/v1/system/* super-admin panel
│   │       └── system_health.py   Legacy /system/health scaffold (shadowed)
│   ├── config.py           Downstream URLs, JWT settings, Redis URL
│   └── main.py
├── tests/
│   └── unit/
├── Dockerfile
└── pyproject.toml
```

---

*Last updated: 2026-06-25 — added the system-control proxy (`/api/v1/system/*`), corrected `POST /api/v1/assets` RBAC to editor-or-admin (2.3.0), completed the registry proxy table, and refreshed the env vars and directory layout.*
