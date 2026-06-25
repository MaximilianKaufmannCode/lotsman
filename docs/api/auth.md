# Auth API Reference

**Service**: `auth-service` (source of truth) via `web-bff` (SPA-facing proxy)
**Base path**: `/api/v1` (BFF-facing; every endpoint below is relative to this prefix)
**Protocol**: REST + JSON, OpenAPI 3.1
**Auth model**: see [ADR-0003](../adr/0003-authentication-and-session-lifecycle.md)

Last updated: 2026-06-25 · product version 2.4.0

---

## Overview

The SPA talks exclusively to `web-bff`. It never calls `auth-service` directly.

```
Browser  →  Nginx (TLS)  →  web-bff  →  auth-service
```

`web-bff` responsibilities on every auth call:

1. Receives the SPA request (Bearer token in `Authorization` where required).
2. Mints a short-lived internal HS256 JWT (`aud=auth-service`, TTL = 60 s) and attaches it as `X-Internal-Token`.
3. Forwards to `auth-service`, translating field names where the SPA contract differs from the backend schema (documented per endpoint).
4. Manages the `refresh` HttpOnly cookie — `auth-service` returns the refresh token in the JSON body; the BFF strips it from the body and re-sets it as a cookie on the way out.
5. Acts as the sole re-MFA chokepoint for sensitive operations: it extracts the `totp_code` from the request body, validates it against `auth-service` `POST /auth/mfa-check`, and only then forwards the upstream call (without `totp_code`).
6. Passes `auth-service` errors through largely unchanged, preserving the no-enumeration property of `401` responses. It forwards the upstream `code` field both as the `X-Error-Code` header and inside the JSON body so the SPA can map typed UX states.

`auth-service` owns all business logic: argon2id password verification, TOTP enrollment and verification, session creation, lockout counters, backup codes, and RS256 JWT issuance.

---

## Auth flow diagram

```mermaid
sequenceDiagram
    autonumber
    actor U as User (SPA)
    participant B as web-bff
    participant A as auth-service
    participant R as Redis
    participant P as Postgres

    U->>B: POST /api/v1/auth/login {email, password}
    B->>A: POST /auth/login (X-Internal-Token aud=auth-service)
    A->>P: CheckLockout(email); verify argon2id hash
    A->>R: SET pending-totp:<ticket> {user_id} ex=300
    A-->>B: 200 {session_ticket} or {enrollment_token}
    B-->>U: 200 {status, next_step, totp_session_token|enrollment_token}

    U->>B: POST /api/v1/auth/totp/verify {totp_session_token, code}
    B->>A: POST /auth/totp/verify {session_ticket, totp_code}
    A->>P: Fernet-decrypt totp_secret_enc; pyotp.verify(code, valid_window=1)
    A->>P: INSERT totp_used_codes (anti-replay by period_index)
    A->>P: INSERT sessions (refresh_hash=sha256(opaque), expires_at=+12h)
    A-->>B: 200 {access_token, refresh_token, backup_codes_warning?}
    B-->>U: 200 {access_token, token_type, backup_codes_warning?}
    Note over B,U: Set-Cookie: refresh=<opaque>; HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth; Max-Age=43200

    Note over U,B: 15 min later — access token near expiry
    U->>B: POST /api/v1/auth/refresh  (cookie only, no Authorization)
    B->>A: POST /auth/refresh (refresh token from cookie)
    A->>P: SELECT session WHERE refresh_hash=sha256(received); rotate
    A-->>B: 200 {access_token, refresh_token (new)}
    B-->>U: 200 {access_token}; Set-Cookie: refresh=<new>
```

### Refresh reuse detection

If a previously-rotated refresh token is replayed:

- `auth-service` revokes **all** sessions for that `user_id`.
- Returns `401`.
- Emits `auth.session.reuse_detected.v1` (severity HIGH) to the outbox.

### Multi-tab coordination and BFF refresh coalescing

The SPA uses `BroadcastChannel('lotsman-auth')` with leader election so exactly one tab calls `POST /api/v1/auth/refresh` at a time; other tabs receive the new access token via the channel.

As a second line of defence, the BFF coalesces concurrent refreshes per refresh-cookie hash: the first request acquires a lock, calls upstream, and caches the result for 5 s; concurrent waiters replay that cached result. This prevents a `Promise.all` / multi-tab race from being misread as token reuse and triggering chain-revocation (see [ADR-0003 §13 Amendment 2026-05-12](../adr/0003-authentication-and-session-lifecycle.md)). The cache is per-process; a horizontally-scaled BFF would need a distributed lock.

---

## Request authentication

| Endpoint class | SPA must send | BFF internal call |
|---|---|---|
| Unauthenticated (`/login`, `/totp/verify`, `/backup-codes/verify`, `/refresh`, and the enrollment-ticket lanes) | Nothing, or an opaque enrollment ticket; the refresh cookie is auto-included by the browser | Internal JWT with a system actor |
| Authenticated (all others) | `Authorization: Bearer <access_jwt>` | Internal JWT with the actor's `sub` + `role` |

State-changing routes (`POST`/`PUT`/`PATCH`/`DELETE`) that lack a Bearer header receive `401` even if a refresh cookie is present. This is the CSRF defence per [ADR-0003 §14](../adr/0003-authentication-and-session-lifecycle.md#14-authorization-headers-and-csrf).

### Re-MFA (sensitive operations)

The BFF is the **sole re-MFA chokepoint**. For every sensitive write (admin user mutations, channel/calendar/import mutations, password change in some lanes, email change), the SPA includes the user's current `totp_code` **in the request body**. The BFF pops `totp_code`, validates it via `POST /api/v1/auth/mfa-check`, and forwards the upstream call **without** `totp_code`. A missing or invalid code returns `401` with `code = "REMFA_REQUIRED"`.

`auth-service` additionally sets a short-lived Redis `mfa-verified` flag keyed by `user_id` when `/mfa-check` succeeds. There is no `re_mfa_token` in any admin request body — see [`/re-mfa`](#post-apiv1authre-mfa) for the (diagnostic) token the BFF returns.

---

## Cookie attributes

The `refresh` cookie is set only by `web-bff` on successful login, enrollment-terminal step, or token refresh. It is never present in JSON response bodies.

| Attribute | Value |
|---|---|
| `HttpOnly` | yes |
| `Secure` | yes (env-tunable; `false` only for local HTTP dev) |
| `SameSite` | `Strict` (env-tunable) |
| `Path` | `/api/v1/auth` |
| `Max-Age` | `43200` (12 hours) |
| `Domain` | omitted — locked to the deployment host exactly |

`Max-Age` mirrors the refresh-token TTL, which is configurable via `REFRESH_TOKEN_TTL_SECONDS` and **must match** in `auth-service` and `web-bff` (both default to `43200`). See [ADR-0003 §13 Amendment 2026-05-12](../adr/0003-authentication-and-session-lifecycle.md).

---

## Internal JWT contract

`web-bff` mints one HS256 JWT per downstream call. `auth-service` validates and discards it; it never leaves the internal network.

| Claim | Value |
|---|---|
| `iss` | `"web-bff"` |
| `aud` | target service, e.g. `"auth-service"` |
| `sub` | actor UUID (string) |
| `role` | `"super_admin"` \| `"admin"` \| `"editor"` \| `"viewer"` \| `"system"` |
| `jti` | UUIDv4 (unique per request) |
| `iat` | unix timestamp |
| `nbf` | same as `iat` (2 s leeway on verify) |
| `exp` | `iat + 60` seconds |

`role = "system"` is the technical role used for inter-service / system-actor calls (e.g. unauthenticated login forwarding); it is not assignable to humans. `auth-service` stores `jti` in Redis (`SET NX ex=ttl_remaining`) and rejects replays with `401`. The signing key is held per-service (`INTERNAL_JWT_KEY_AUTH`, min 32 chars); startup fails fast if shorter. See [ADR-0003 §10](../adr/0003-authentication-and-session-lifecycle.md#10-internal-jwt-per-service-hs256-keys).

---

## Access JWT claims (RS256, issued by auth-service)

Verified by `web-bff` using the public key at `JWT_PUBLIC_KEY_PATH`.

| Claim | Type | Description |
|---|---|---|
| `iss` | string | `"lotsman-auth"` |
| `aud` | string | `"lotsman-spa"` |
| `sub` | string | User UUID |
| `email` | string | Lowercased email |
| `role` | string | `"super_admin"` \| `"admin"` \| `"editor"` \| `"viewer"` |
| `sid` | string | Session UUID (links to `auth.sessions.id`) |
| `jti` | string | UUIDv4 |
| `iat` | int | Issued-at unix timestamp |
| `nbf` | int | Not-before (same as `iat`) |
| `exp` | int | `iat + 900` (15 min) |
| `kid` | string (header) | Key ID for rotation support |

The role values match the RBAC matrix (`super_admin`/`admin`/`editor`/`viewer`); the schema pattern is `^(super_admin|admin|editor|viewer)$`. The SPA-facing admin endpoints below gate on `role == "admin"` specifically; the `super_admin` contour is described in [ADR-0004](../adr/0004-two-tier-administration.md).

---

## `/api/v1/auth/*` endpoints

### `POST /api/v1/auth/login`

Step 1 of login. Validates credentials; returns the next required step.

No `Authorization` header required.

**Request body** (SPA → BFF):
```json
{
  "email": "alice@company.ru",
  "password": "my-strong-password"
}
```

**BFF → auth-service translation**: passes through. `auth-service` `LoginRequest` also defines optional `totp_code` and `backup_code` fields; they are unused in this two-phase flow.

**Response — TOTP required** (existing user with TOTP enrolled):
```json
{
  "status": "totp_required",
  "next_step": "verify_totp",
  "totp_session_token": "<opaque-ticket>"
}
```

**Response — enrollment required** (first login, TOTP not yet configured):
```json
{
  "status": "enrollment_required",
  "next_step": "enroll_totp",
  "enrollment_token": "<opaque-token>"
}
```

`next_step` is part of the SPA contract: the SPA's `LoginResponse` union keys on exactly the literals `"verify_totp"` / `"enroll_totp"` to drive routing. (`auth-service` returns only `session_ticket` / `enrollment_token`; the `status` and `next_step` fields are added by the BFF.)

**Error responses**:

| HTTP | `detail` | Cause |
|---|---|---|
| `401` | `"Invalid credentials"` | Wrong password, wrong TOTP, locked account, inactive account, unknown email — all return an identical body (no enumeration) |
| `429` | — | Nginx rate limit (5 req/min/IP on `/login`) |

**Login attempts** are written to the `auth.login_attempts` table with an `outcome` (used for the lockout policy, [ADR-0003 §12](../adr/0003-authentication-and-session-lifecycle.md)). This is a table, not an outbox event.

---

### `POST /api/v1/auth/totp/verify`

Step 2 of login. Completes authentication with a TOTP code.

No `Authorization` header required.

**Request body** (SPA → BFF):
```json
{
  "totp_session_token": "<opaque-ticket>",
  "code": "123456"
}
```

**BFF → auth-service translation**:
```json
{
  "session_ticket": "<opaque-ticket>",
  "totp_code": "123456"
}
```

**Response** (200):
```json
{
  "access_token": "<RS256-jwt>",
  "token_type": "Bearer",
  "backup_codes_warning": 2
}
```

`backup_codes_warning` is the count of remaining unused backup codes; present only when low, otherwise `null`. The BFF sets the `refresh` HttpOnly cookie and strips `refresh_token` from the body.

**Error responses**:

| HTTP | Cause |
|---|---|
| `401` | Invalid or expired session ticket, wrong TOTP code, account locked |

**Audit event**: `auth.user.logged_in.v1` (payload `method = "totp"`).

---

### `POST /api/v1/auth/backup-codes/verify`

Step 2 of login using a backup code instead of TOTP.

No `Authorization` header required.

**Request body** (SPA → BFF):
```json
{
  "totp_session_token": "<opaque-ticket>",
  "code": "A1B2-C3D4"
}
```

**BFF → auth-service translation**: the BFF posts to `auth-service` `POST /auth/totp/verify` with `{session_ticket, backup_code}` (no `totp_code`). The `VerifyTotp` use case handles the backup-code path when `backup_code` is supplied.

> **Contract note (verify against code before relying on this):** `auth-service` exposes a single `/auth/totp/verify` route bound to a schema that requires `totp_code` and does **not** declare `backup_code`. The BFF backup-code client targets the same route with a `backup_code` body. Treat the exact upstream backup-code request schema as unsettled until reconciled in OpenAPI; do not document a stable `backup_code` field on `/auth/totp/verify` as confirmed.

**Response**: same shape as `/totp/verify` (200 with access token + refresh cookie). Each backup code is single-use; the matched `auth.totp_backup_codes` row is marked used.

**Audit event**: `auth.user.logged_in.v1` (payload `method = "backup_code"`).

---

### `POST /api/v1/auth/totp/enroll`

Step 1 of TOTP enrollment. Returns the secret and `otpauth://` URL for QR rendering.

Anonymous enrollment-ticket lane (ADR-0008 D3): the SPA sends the enrollment ticket in the body field `enrollment_token`; an `Authorization: Bearer <opaque-token>` is accepted as a fallback (the value is treated as an opaque string, never decoded as a JWT). Identity is resolved from the ticket only.

**Request body**:
```json
{ "enrollment_token": "<opaque-token>" }
```

**Response** (200):
```json
{
  "secret_b32": "JBSWY3DPEHPK3PXP",
  "otpauth_url": "otpauth://totp/Лоцман:alice%40company.ru?secret=JBSWY3DPEHPK3PXP&issuer=%D0%9B%D0%BE%D1%86%D0%BC%D0%B0%D0%BD&algorithm=SHA1&digits=6&period=30"
}
```

The QR is rendered client-side. The pending secret lives in Redis (5-minute TTL); `auth.users.totp_secret_enc` is **not** written until `/totp/enroll/confirm` succeeds. The request body MUST NOT be logged.

See [ADR-0008](../adr/0008-first-login-enrollment-ticket-exchange.md).

---

### `POST /api/v1/auth/totp/enroll/confirm`

Step 2 of TOTP enrollment. Verifies the first code, persists the Fernet-encrypted secret, and generates 10 backup codes.

Anonymous enrollment-ticket lane (same ticket extraction as `/totp/enroll`).

**Request body**:
```json
{
  "enrollment_token": "<opaque-token>",
  "code": "123456"
}
```

**Response** (200) — backup codes (10), shown **once**:
```json
{
  "backup_codes": ["A1B2-C3D4", "E5F6-7890", "..."],
  "access_token": null,
  "refresh_token": null
}
```

On the **terminal branch** (`must_change_password == false`), enroll/confirm completes login: `auth-service` returns a real `access_token` + `refresh_token`; the BFF promotes the refresh to the cookie and strips it from the body. On the **non-terminal branch** (`must_change_password == true`), only `backup_codes` are returned and the user is routed to the forced password change. Backup codes are stored as argon2id hashes; plaintext is never persisted.

**Error responses**:

| HTTP | Cause |
|---|---|
| `400` | No pending enrollment in Redis (expired), or the code did not verify |

**Audit event**: `auth.user.totp_enrolled.v1`.

---

### `POST /api/v1/auth/refresh`

Rotates the refresh token. Cookie-only — no `Authorization` header accepted or required.

**Request**: no body. The browser sends the `refresh` cookie automatically.

**Response** (200):
```json
{
  "access_token": "<RS256-jwt>",
  "token_type": "Bearer"
}
```

BFF sets a new `refresh` cookie. The old session row in `auth.sessions` is marked revoked. Concurrent refreshes are coalesced BFF-side (see above).

**Error responses**:

| HTTP | Cause |
|---|---|
| `401` | No cookie, expired session, session revoked |
| `401` | Reuse detected — rotated token replayed; all user sessions chain-revoked |

**Audit events**: `auth.session.rotated.v1` on success; `auth.session.reuse_detected.v1` (severity HIGH) on replay.

---

### `POST /api/v1/auth/logout`

Revokes the current session. Requires `Authorization: Bearer`.

**Request**: no body.

**Response**: `204 No Content`.

BFF clears the `refresh` cookie regardless of whether the upstream call succeeds. The access JWT remains valid until its `exp` (up to 15 min); this gap is accepted per [ADR-0003 §13](../adr/0003-authentication-and-session-lifecycle.md#13-session-revocation-surface).

**Audit event**: `auth.session.revoked.v1`.

---

### `POST /api/v1/auth/backup-codes/regenerate`

Generates a new set of 10 backup codes, invalidating all prior ones.

Requires `Authorization: Bearer`.

**Request**: no body.

**Response** (200):
```json
{
  "backup_codes": ["A1B2-C3D4", "...", "..."]
}
```

All previous `auth.totp_backup_codes` rows for the user are replaced.

**Audit event**: `auth.user.backup_codes_regenerated.v1`.

---

### `GET /api/v1/auth/sessions/me`

Returns the authenticated user's active sessions.

Requires `Authorization: Bearer`. **BFF → auth-service translation**: `GET /auth/sessions`.

**Response** (200) — array of:
```json
[
  {
    "id": "01935f3c-a2b1-7f00-8012-0123456789ab",
    "user_id": "01935f3c-a2b1-7000-8000-000000000001",
    "user_agent": "Mozilla/5.0 (Macintosh; ...)",
    "ip_address": "10.0.0.42",
    "created_at": "2026-06-25T09:00:00Z",
    "expires_at": "2026-06-25T21:00:00Z",
    "is_current": false
  }
]
```

`is_current` is intended to flag the session behind the current Bearer token. The access JWT does carry the session id (`sid`), but `sid` is **not** propagated into the internal JWT, so `auth-service` receives `current_session_id` as the zero UUID and `is_current` is therefore `false` for every row until `sid` is threaded through the internal JWT.

---

### `DELETE /api/v1/auth/sessions/{session_id}`

Revokes a specific session. Users can only revoke their own sessions (enforced by `auth-service`).

Requires `Authorization: Bearer`. **Path parameter**: `session_id` (UUID).

**Response**: `204 No Content`.

**Audit event**: `auth.session.revoked.v1`.

---

### `POST /api/v1/auth/re-mfa`

Verifies the user's current TOTP code so the SPA can proceed to a sensitive operation.

Requires `Authorization: Bearer`.

**Request body** (SPA → BFF):
```json
{ "code": "123456" }
```

**BFF → auth-service translation**: `POST /auth/mfa-check` with `{"totp_code": "123456"}`.

**Response** (200):
```json
{
  "re_mfa_token": "<subject>:<jti>",
  "mfa_verified": true
}
```

On success, `auth-service` sets a short-lived Redis `mfa-verified` flag keyed by `user_id`. The real gating model is **the BFF re-MFA chokepoint** (`totp_code` re-supplied per write; see [Re-MFA](#re-mfa-sensitive-operations)). `re_mfa_token` is a diagnostic/compatibility artefact synthesised by the BFF from the actor's `subject` and `jti`; it is **not** consumed by any subsequent endpoint and must **not** be placed in admin request bodies.

**Error responses**:

| HTTP | Cause |
|---|---|
| `403` | TOTP verification failed (`mfa_verified == false`) |
| `401` | Missing or invalid Bearer token |

---

### `POST /api/v1/auth/password/change`

Changes the user's password.

**BFF → auth-service path**: `POST /auth/change-password`.

The BFF selects one of three credential lanes; the SPA sends **only** `{new_password}` plus its credential (no `current_password`, no `re_mfa_token`):

1. **Body `enrollment_token` present** → enrollment-ticket lane (forced-enrollment terminal step). Identity resolved from the ticket in `auth-service`.
2. **`Authorization: Bearer` decodes as a valid RS256 access JWT** → normal actor-JWT lane.
3. **`Authorization: Bearer` is an opaque non-JWT** → enrollment-ticket lane (SPA first-login compatibility, ADR-0008 D7 fallback).

If no usable credential is present → `401`.

**Request body** (SPA → BFF, normal lane):
```json
{ "new_password": "new-password-min-12-chars" }
```

`auth-service` `ChangePasswordRequest` contains only `new_password`; the **current password is not verified** by this endpoint. In the normal actor-JWT lane the access-control gate is the actor JWT (and the forced-change guard `must_change_password` for the ticket lane), not a current-password check.

**Response** (200):
```json
{ "detail": "Password changed successfully" }
```

On a lane that issues a fresh session (e.g. the forced-enrollment terminal step), `auth-service` returns an access + refresh pair; the BFF promotes the refresh to the cookie and returns only the access token.

**Validation**: `new_password` is 12–1024 characters. A local HIBP top-list check runs server-side; a breached password is rejected.

**Audit event**: `auth.user.password_changed.v1`.

See [ADR-0008](../adr/0008-first-login-enrollment-ticket-exchange.md) for the lane design.

---

## `/api/v1/auth/me/*` endpoints (self-service)

All `/me` routes require `Authorization: Bearer` and act on the authenticated user only (ownership enforced upstream). `GET`/`PATCH /me` and `change-email/*` and `saved-filters/*` are served by `auth-service`; `test-email`, `notification-prefs`, and the in-app `notifications` feed are proxied by the BFF to `notification-service`.

### `GET /api/v1/auth/me`

Returns the authenticated user's full profile.

**Response** (200):
```json
{
  "id": "01935f3c-a2b1-7000-8000-000000000001",
  "email": "alice@company.ru",
  "full_name": "Алиса Иванова",
  "role": "editor",
  "is_active": true,
  "must_change_password": false,
  "last_login_at": "2026-06-25T10:30:00Z",
  "created_at": "2026-06-01T08:00:00Z",
  "updated_at": "2026-06-25T10:30:00Z",
  "totp_enrolled": true,
  "is_locked": false,
  "ui_font_scale": 100
}
```

### `PATCH /api/v1/auth/me`

Updates the user's `full_name` and/or `ui_font_scale`. Email is read-only for the user (managed by administrators).

**Request body** (SPA → BFF):
```json
{ "full_name": "Алиса Петрова", "ui_font_scale": 120 }
```

- `full_name` — required by the upstream schema (1–200 chars).
- `ui_font_scale` — optional self-service UI font-size preference (feature added in 2.1.0). Integer percent of the base size, range **80–150**, default **100**; the BFF forwards it only when an integer is present, and `null`/absent leaves it unchanged.

**Response** (200): the updated `UserResponse` (same shape as `GET /me`).

**Audit event**: `auth.user.profile_updated.v1` — emitted only when a field actually changes.

### `POST /api/v1/auth/me/change-email/request`

Step 1 of self-service email change. **Re-MFA required** (BFF chokepoint: `totp_code` in the body).

**Request body** (SPA → BFF):
```json
{ "new_email": "alice.new@company.ru", "totp_code": "123456" }
```

**Response** (200): `{request_id, code_ttl_seconds, masked_new_email}`. The raw verification code is emailed to `new_email` only — never returned.

**Error responses**: `401`/`REMFA_REQUIRED` (missing/invalid TOTP); `503`/`EMAIL_CHANNEL_REQUIRED` (no email channel); `409`/`EMAIL_ALREADY_TAKEN`; `422` (same as current email / invalid format).

**Audit event**: `auth.user.email_change_requested.v1` (payload carries the masked email only).

### `POST /api/v1/auth/me/change-email/confirm`

Step 2: confirm with the 8-digit code emailed to the new address. **No re-MFA** — the email code is the second factor.

**Request body** (SPA → BFF):
```json
{ "request_id": "<id from step 1>", "verification_code": "12345678" }
```

**Response** (200): `{email: "<new email>"}`. Sessions are not invalidated; existing access JWTs keep the old email claim until the next refresh (≤ 15 min).

**Error responses**: `404` (request not found / expired); `401` (wrong code, with `attempts_remaining` in `detail`).

**Audit event**: `auth.user.email_changed.v1` (full before/after emails).

### `POST /api/v1/auth/me/test-email`

Sends a diagnostic email to the authenticated user's own inbox (BFF resolves the recipient server-side from the profile). No re-MFA. Rate-limited to one per 60 s per user (`429` with `Retry-After` otherwise). Proxies to `notification-service`.

**Request**: no body. **Response** (200): `{sent: true, recipient: "<email>"}`.

### Notification preferences — `GET` / `PUT /api/v1/auth/me/notification-prefs`

Thin proxy to `notification-service` (ADR-0011). Always scoped to the authenticated user. `PUT` body validation (e.g. `email_mode`, category sanitisation) is enforced downstream.

### In-app notification feed (ADR-0011 §D6)

Proxies to `notification-service`, scoped to the authenticated user:

| Method + path | Purpose |
|---|---|
| `GET /api/v1/auth/me/notifications` | List feed (`limit`, `offset`) |
| `GET /api/v1/auth/me/notifications/unread-count` | Unread count |
| `POST /api/v1/auth/me/notifications/{notification_id}/read` | Mark one as read |
| `POST /api/v1/auth/me/notifications/read-all` | Mark all as read |

### Saved filters — `/api/v1/auth/me/saved-filters` (registry-filters feature)

Per-user named filter presets, served by `auth-service`. Up to 20 per user; names unique per user; one default.

| Method + path | Purpose | Audit event |
|---|---|---|
| `GET /api/v1/auth/me/saved-filters` | List presets (default first, then alphabetical) | — |
| `POST /api/v1/auth/me/saved-filters` | Create (`201`) — body `{name, filter_json, is_default?}` | `auth.user.filter_preset_saved.v1` |
| `PATCH /api/v1/auth/me/saved-filters/{filter_id}` | Partial update (any of `name`, `filter_json`, `is_default`) | `auth.user.filter_preset_updated.v1` |
| `DELETE /api/v1/auth/me/saved-filters/{filter_id}` | Hard-delete (`204`) | `auth.user.filter_preset_deleted.v1` |

---

## `/api/v1/admin/*` endpoints

All admin endpoints require `Authorization: Bearer` with `role == "admin"`; the BFF returns a fast `403` for non-admins before any upstream call. Write operations are re-MFA gated at the BFF: the SPA includes the admin's current `totp_code` in the request body, the BFF validates it via `/auth/mfa-check` and forwards without it (see [Re-MFA](#re-mfa-sensitive-operations)). A missing/invalid code returns `401`/`REMFA_REQUIRED`.

> **User-management endpoints only are documented here.** The same BFF router also hosts admin proxies to `notification-service` and `registry-service` under `/api/v1/admin/*` — channel config (`/channels…`), calendar subscriptions (`/calendar-subscriptions…`), document-type custom fields (`/document-types/{code}/custom-fields`), xlsx import (`/import/preview`, `/import/confirm`), and notification history (`/notifications/history`). Those are out of scope for this file; see the [Registry API](registry.md) and the notification/calendar ADRs ([ADR-0005](../adr/0005-exchange-calendar-integration.md), [ADR-0011](../adr/0011-notifications-expansion.md)). They share the same re-MFA chokepoint for writes.

### `GET /api/v1/admin/users`

Lists all users. Read-only — no re-MFA.

**Response** (200) — array of `UserResponse`:
```json
[
  {
    "id": "01935f3c-a2b1-7000-8000-000000000001",
    "email": "alice@company.ru",
    "full_name": "Алиса Иванова",
    "role": "editor",
    "is_active": true,
    "must_change_password": false,
    "last_login_at": "2026-06-25T10:30:00Z",
    "created_at": "2026-06-01T08:00:00Z",
    "updated_at": "2026-06-25T10:30:00Z",
    "totp_enrolled": true,
    "is_locked": false,
    "ui_font_scale": 100
  }
]
```

`totp_enrolled` (the user has a real TOTP secret, i.e. not pending) and `is_locked` (admin Redis lockout) let the SPA render pending-user and lockout badges.

### `GET /api/v1/admin/users/{user_id}`

Single user by UUID. Read-only — no re-MFA. Same `UserResponse` shape. `404` if not found.

### `POST /api/v1/admin/users` — invite user

Invites a new user (ADR-0004 Phase 2b; replaces the old "create user"). **Re-MFA required.**

**Request body** (SPA → BFF):
```json
{
  "email": "bob@company.ru",
  "full_name": "Боб Петров",
  "role": "viewer",
  "delivery": "show-otp",
  "totp_code": "123456"
}
```

- `role` — `super_admin` \| `admin` \| `editor` \| `viewer` (schema pattern).
- `delivery` — `"auto"` \| `"show-otp"`; default `"show-otp"`. The BFF strips `totp_code` after the re-MFA check.

**Response (201) depends on `delivery`:**

`delivery = "show-otp"` — OTP returned for the admin to relay out-of-band (shown once):
```json
{
  "user_id": "01935f3c-a2b1-7f00-8012-aabbccddeeff",
  "otp": "WXYZ-1234",
  "otp_ttl_minutes": 10
}
```

`delivery = "auto"` — OTP delivered via the first enabled channel (priority `email` → `telegram` → `dion`); not returned in the body:
```json
{
  "user_id": "01935f3c-a2b1-7f00-8012-aabbccddeeff",
  "channel_used": "email",
  "invitation_id": "01935f3c-a2b1-7f00-8012-112233445566"
}
```

**Error responses**:

| HTTP | Cause |
|---|---|
| `409` | `delivery=auto` and no channel is enabled (`NoEnabledChannelError`) |
| `409` | Email already exists (`UserAlreadyExistsError`) |
| `401` | Missing/invalid re-MFA (`REMFA_REQUIRED`) |
| `403` | Caller is not admin |

**Audit events**: `auth.user.invited.v1`; plus `notification.invite.requested.v1` when `delivery=auto`.

### `PATCH /api/v1/admin/users/{user_id}`

Partial update. The BFF fans out to distinct `auth-service` routes based on the body fields. **Re-MFA required.**

| Body field | auth-service route | Audit event |
|---|---|---|
| `{"role": "<role>"}` | `PATCH /admin/users/{id}/role` | `auth.user.role_changed.v1` |
| `{"active": false}` | `POST /admin/users/{id}/deactivate` | `auth.user.deactivated.v1` (+ all sessions revoked) |
| `{"active": true}` | `POST /admin/users/{id}/reactivate` | `auth.user.activated.v1` |
| `{"full_name": "<name>"}` | `PATCH /admin/users/{id}/profile` | `auth.user.profile_updated.v1` |

`totp_code` is consumed by the BFF re-MFA gate. **Response**: the updated `UserResponse` (200), or `{"detail": "Updated"}` for `204` upstream paths. `400` if the body has no supported field.

### `DELETE /api/v1/admin/users/{user_id}`

Permanent soft-delete (hides the user and frees the email for reuse). **Re-MFA required** (`totp_code` in body). Revokes the user's sessions.

**Response**: `204 No Content`. **Audit events**: `auth.user.deleted.v1` (+ `auth.session.revoked_all.v1`).

### `POST /api/v1/admin/users/{user_id}/invite` — re-invite

Re-invites a pending (not-yet-activated) user — invalidates the old OTP and issues a new one (US-10). **Re-MFA required.**

**Request body**: `{ "delivery": "auto" | "show-otp", "totp_code": "..." }` — `delivery` defaults to `"auto"`.
**Response** (200): same two shapes as the invite response (auto vs show-otp).

**Error responses**: `409` (`NoEnabledChannelError`); `409` (`UserAlreadyActivatedError` — user already activated).
**Audit event**: `auth.invitation.resent.v1` (+ `notification.invite.requested.v1` for `delivery=auto`).

### `POST /api/v1/admin/users/{user_id}/lockout`

Instant kill-switch: adds the user to a Redis lockout flag and revokes all sessions. **Re-MFA required.**

**Response**: `204 No Content`. The BFF/auth-service deny subsequent requests for a locked user. **Audit events**: `auth.user.locked.v1` (+ `auth.session.revoked_all.v1`).

### `DELETE /api/v1/admin/users/{user_id}/lockout`

Removes the manual lockout flag. **Re-MFA required.** Admin lockouts have no TTL — they require explicit unlock (unlike the automatic failed-login lockout in [ADR-0003 §12](../adr/0003-authentication-and-session-lifecycle.md)).

**Response**: `204 No Content`. **Audit event**: `auth.account.unlocked.v1`.

### `GET /api/v1/admin/users/{user_id}/sessions`

Lists all active sessions for a target user. Read-only — no re-MFA. Array of `SessionResponse` (same shape as `/auth/sessions/me`; `is_current` behaves as noted above).

### `DELETE /api/v1/admin/users/{user_id}/sessions`

Revokes **all** active sessions for a target user (US-15). **Re-MFA required.**

**Response**: `204 No Content` (the upstream returns `{revoked_count}`). **Audit event**: `auth.session.revoked_all.v1`.

### `DELETE /api/v1/admin/users/{user_id}/sessions/{session_id}`

Revokes **one** specific session of a target user (US-105). **Re-MFA required.**

**Response**: `204 No Content`. **Audit event**: `auth.session.revoked.v1`.

### `POST /api/v1/admin/users/{user_id}/totp/reset`

Resets a target user's TOTP secret to the sentinel value, forcing re-enrollment on next login (US-16).

This endpoint uses **inline** re-MFA: the admin supplies their own current TOTP code, which the `auth-service` use case verifies directly (it does not rely on the Redis `mfa-verified` flag). The BFF forwards the same code as `admin_totp_code`.

**Request body** (SPA → BFF):
```json
{ "admin_totp_code": "123456" }
```

**Response**: `204 No Content` (the BFF returns `{"detail": "TOTP reset"}` if the upstream body is empty).

On reset: `auth.users.totp_secret_enc` is set to the sentinel, all the target's sessions are revoked, and all `auth.totp_backup_codes` rows are deleted.

**Error responses**: `401` (admin TOTP wrong); `403` (caller not admin); `404` (target not found).
**Audit events**: `auth.user.totp_reset.v1` (+ `auth.session.revoked_all.v1`).

### `POST /api/v1/admin/users/{user_id}/password/reset`

Generates a new out-of-band OTP for a target user, marks the account "must change password", and revokes all sessions (US-20). **Re-MFA required** (`totp_code` in body).

**Response** (200):
```json
{ "oob_otp": "WXYZ-1234" }
```

The admin relays the OTP to the user out-of-band. After logging in with it, the user is forced through the password-change flow before regaining normal access (`must_change_password == true` on the profile).

**Audit events**: `auth.user.password_reset.v1` (+ `auth.session.revoked.v1`).

---

## Reference

- [ADR-0003 Authentication and Session Lifecycle](../adr/0003-authentication-and-session-lifecycle.md) — token/session model, TTLs (§7, §8), cookie attributes (§9), internal JWT (§10), lockout (§12), revocation surface (§13), CSRF (§14), Amendment 2026-05-12 (configurable TTL + BFF refresh coalescing).
- [ADR-0004 Two-tier administration](../adr/0004-two-tier-administration.md) — super-admin/space-admin contours, channel-credential storage, auto-invite (§5).
- [ADR-0008 First-login enrollment-ticket exchange](../adr/0008-first-login-enrollment-ticket-exchange.md) — anonymous enrollment lanes for `/totp/enroll*` and `/password/change`.
- [ADR-0011 Notifications expansion](../adr/0011-notifications-expansion.md) — notification preferences and in-app feed proxied under `/auth/me`.
- [Registry API](registry.md) — registry/admin routes hosted alongside the admin proxy.

_Last updated: 2026-06-25_
</content>
</invoke>
