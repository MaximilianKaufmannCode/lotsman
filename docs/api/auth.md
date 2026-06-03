# Auth API Reference

**Service**: `auth-service` (source of truth) via `web-bff` (SPA-facing proxy)
**Base path**: `/api/v1` (BFF-facing; all endpoints below are relative to this prefix)
**Protocol**: REST + JSON, OpenAPI 3.1
**Auth model**: see [ADR-0003](../adr/0003-authentication-and-session-lifecycle.md)

Last updated: 2026-05-06

---

## Overview

The SPA talks exclusively to `web-bff`. It never calls `auth-service` directly.

```
Browser  →  Nginx (TLS)  →  web-bff  →  auth-service
```

`web-bff` responsibilities on every auth call:

1. Receives the SPA request (Bearer token in `Authorization` header where required).
2. Mints a short-lived internal HS256 JWT (`aud=auth-service`, TTL=60 s) and attaches it as `X-Internal-Token`.
3. Forwards to `auth-service`; translates field names where the SPA contract differs from the backend schema (documented per endpoint below).
4. Manages the `refresh` HttpOnly cookie — strips the refresh token from JSON response bodies and re-sets it as a cookie on the way back out.
5. Passes error responses from `auth-service` through without modification (preserves the no-enumeration property of 401 responses).

`auth-service` owns all business logic: argon2id password verification, TOTP enrollment and verification, session creation, lockout counters, backup codes, JWT issuance.

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
    B-->>U: 200 {status:"totp_required", totp_session_token} or {status:"enrollment_required"}

    U->>B: POST /api/v1/auth/totp/verify {totp_session_token, code}
    B->>A: POST /auth/totp/verify {session_ticket, totp_code}
    A->>P: Fernet-decrypt totp_secret_enc; pyotp.verify(code, valid_window=1)
    A->>P: INSERT totp_used_codes (anti-replay by period_index)
    A->>P: INSERT sessions (refresh_hash=sha256(opaque), expires_at=+7d)
    A-->>B: 200 {access_token, refresh_token, backup_codes_warning?}
    B-->>U: 200 {access_token, token_type, backup_codes_warning?}
    Note over B,U: Set-Cookie: refresh=<opaque>; HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth; Max-Age=604800

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
- Emits `auth.session.reuse_detected.v1` (severity=high) to the outbox.

### Multi-tab coordination

The SPA uses `BroadcastChannel('lotsman-auth')` with leader election so exactly one tab calls `POST /api/v1/auth/refresh` at any time. Other tabs receive the new access token via the channel. This is why `auth-service` enforces strict rotation with no grace window.

---

## Request authentication

| Endpoint class | SPA must send | BFF internal call |
|---|---|---|
| Unauthenticated (`/login`, `/totp/verify`, `/backup-codes/verify`, `/refresh`) | Nothing (cookie auto-included by browser) | Internal JWT with system actor |
| Authenticated (all others) | `Authorization: Bearer <access_jwt>` | Internal JWT with actor's `sub` + `role` |

State-changing routes (`POST`/`PUT`/`PATCH`/`DELETE`) that lack a Bearer header receive `401` even if a refresh cookie is present. This is the CSRF defence per [ADR-0003 §14](../adr/0003-authentication-and-session-lifecycle.md).

---

## Cookie attributes

The `refresh` cookie is set only by `web-bff` on successful login or token refresh. It is never present in JSON response bodies.

| Attribute | Value |
|---|---|
| `HttpOnly` | yes |
| `Secure` | yes |
| `SameSite` | `Strict` |
| `Path` | `/api/v1/auth` |
| `Max-Age` | `604800` (7 days) |
| `Domain` | omitted — locked to `lotsman.example.com` exactly |

---

## Internal JWT contract

`web-bff` mints one HS256 JWT per downstream call. `auth-service` validates and discards it; it never leaves the internal network.

| Claim | Value |
|---|---|
| `iss` | `"web-bff"` |
| `aud` | `"auth-service"` |
| `sub` | actor UUID (string) |
| `role` | `"admin"` \| `"editor"` \| `"viewer"` |
| `jti` | UUIDv4 (unique per request) |
| `iat` | unix timestamp |
| `nbf` | same as `iat` |
| `exp` | `iat + 60` seconds |

`auth-service` stores `jti` in Redis (`SET NX ex=ttl_remaining`) and rejects replays with `401`. The key is held by `INTERNAL_JWT_KEY_AUTH` (min 32 chars); startup fails fast if shorter. See [ADR-0003 §10](../adr/0003-authentication-and-session-lifecycle.md) for the per-service keying strategy.

---

## Access JWT claims (RS256, issued by auth-service)

Verified by `web-bff` using the public key at `JWT_PUBLIC_KEY_PATH`.

| Claim | Type | Description |
|---|---|---|
| `iss` | string | `"lotsman-auth"` |
| `aud` | string | `"lotsman-spa"` |
| `sub` | string | User UUID |
| `email` | string | Lowercased email |
| `role` | string | `"admin"` \| `"editor"` \| `"viewer"` |
| `sid` | string | Session UUID (links to `auth.sessions.id`) |
| `jti` | string | UUIDv4 |
| `iat` | int | Issued-at unix timestamp |
| `nbf` | int | Not-before (same as `iat`) |
| `exp` | int | `iat + 900` (15 min) |
| `kid` | string | Key ID for rotation support |

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

**BFF → auth-service translation**: passes through as-is. `auth-service` `LoginRequest` also accepts `totp_code` and `backup_code` fields; those are unused in this two-phase flow.

**Response — TOTP required** (existing user with TOTP enrolled):
```json
{
  "status": "totp_required",
  "totp_session_token": "<opaque-ticket>"
}
```

**Response — enrollment required** (first login, TOTP not yet configured):
```json
{
  "status": "enrollment_required",
  "enrollment_token": "<opaque-token>"
}
```

**Error responses**:

| HTTP | `detail` | Cause |
|---|---|---|
| `401` | `"Invalid credentials"` | Wrong password, wrong TOTP, locked account, inactive account, unknown email — all return identical body (no enumeration) |
| `429` | — | Nginx rate limit (5 req/min/IP on `/login`) |

**Audit event**: `auth.login_attempts` row with `outcome` = `success` \| `failed_password` \| `locked`.

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

`backup_codes_warning` is the count of remaining unused backup codes. Present only when ≤ 2 codes remain; `null` otherwise.

The BFF sets the `refresh` HttpOnly cookie (see Cookie attributes above). The `refresh_token` field is stripped from the JSON body before returning to the SPA.

**Error responses**:

| HTTP | Cause |
|---|---|
| `401` | Invalid or expired session ticket, wrong TOTP code, account locked |

**Related**: US-2, US-3. **Audit event**: `auth.user.logged_in.v1`.

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

**BFF → auth-service translation**: calls `auth-service` `/auth/totp/verify` with `backup_code` field (not `totp_code`).

**Response**: identical to `/totp/verify` (200 with access token + refresh cookie set).

**Notes**: each backup code is single-use. The `used_at` column is set on the matched `auth.totp_backup_codes` row.

**Related**: US-4. **Audit event**: `auth.user.logged_in.v1`.

---

### `POST /api/v1/auth/totp/enroll`

Step 1 of TOTP enrollment. Returns the secret and `otpauth://` URL for QR rendering.

Requires `Authorization: Bearer <token>` (enrollment-scoped token on first login, or full access token for profile rotation).

**Request body**: none.

**Response** (200):
```json
{
  "secret_b32": "JBSWY3DPEHPK3PXP",
  "otpauth_url": "otpauth://totp/Лоцман:alice%40company.ru?secret=JBSWY3DPEHPK3PXP&issuer=%D0%9B%D0%BE%D1%86%D0%BC%D0%B0%D0%BD&algorithm=SHA1&digits=6&period=30"
}
```

The QR code is rendered client-side from `otpauth_url`. The secret is held in Redis (`enrollment:<user_id>`) with a 5-minute TTL. `auth.users.totp_secret_enc` is **not** written until `/enroll/confirm` succeeds.

**Related**: US-1, US-6.

---

### `POST /api/v1/auth/totp/enroll/confirm`

Step 2 of TOTP enrollment. Verifies the first code, persists the Fernet-encrypted secret, and generates 10 backup codes.

Requires `Authorization: Bearer <token>`.

**Request body** (SPA → BFF):
```json
{
  "code": "123456"
}
```

**Response** (200):
```json
{
  "backup_codes": [
    "A1B2-C3D4",
    "E5F6-7890",
    "AB12-CD34",
    "EF56-7890",
    "1234-ABCD",
    "5678-EF01",
    "23AB-45CD",
    "67EF-8901",
    "ABCD-1234",
    "5678-90EF"
  ]
}
```

Backup codes are shown **once**. They are stored as argon2id hashes in `auth.totp_backup_codes`. The plaintext is never persisted.

**Error responses**:

| HTTP | Cause |
|---|---|
| `400` | No pending enrollment in Redis (expired after 5 min), or code did not verify |
| `401` | Missing or invalid Bearer token |

**Related**: US-1. **Audit event**: `auth.user.totp_enrolled.v1`.

---

### `POST /api/v1/auth/refresh`

Rotates the refresh token. Cookie-only endpoint — no `Authorization` header accepted or required.

**Request**: no body. The browser sends the `refresh` cookie automatically.

**Response** (200):
```json
{
  "access_token": "<RS256-jwt>",
  "token_type": "Bearer"
}
```

BFF sets a new `refresh` cookie. The old session row in `auth.sessions` is marked `revoked_at = now()`.

**Error responses**:

| HTTP | Cause |
|---|---|
| `401` | No cookie, expired session, session revoked |
| `401` | Reuse detected — rotated token replayed; all user sessions chain-revoked |

**Audit event (reuse)**: `auth.session.reuse_detected.v1` (severity=high).

---

### `POST /api/v1/auth/logout`

Revokes the current session. Requires `Authorization: Bearer`.

**Request**: no body.

**Response**: `204 No Content`.

BFF clears the `refresh` cookie (`Max-Age=0`) regardless of whether the upstream call succeeds. The access JWT remains valid until its `exp` (up to 15 min); this gap is accepted per [ADR-0003 §13](../adr/0003-authentication-and-session-lifecycle.md).

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

All previous `auth.totp_backup_codes` rows for the user are replaced. This operation requires that the user has previously passed re-MFA (the auth-service checks the Redis `mfa-verified` flag set by `/mfa-check`).

**Related**: US-5. **Audit event**: `auth.user.backup_codes_regenerated.v1`.

---

### `GET /api/v1/auth/sessions/me`

Returns the authenticated user's active sessions.

Requires `Authorization: Bearer`.

**BFF → auth-service translation**: `GET /auth/sessions`.

**Response** (200) — array of:
```json
[
  {
    "id": "01935f3c-a2b1-7f00-8012-0123456789ab",
    "user_id": "01935f3c-a2b1-7000-8000-000000000001",
    "user_agent": "Mozilla/5.0 (Macintosh; ...)",
    "ip_address": "10.0.0.42",
    "created_at": "2026-05-06T09:00:00Z",
    "expires_at": "2026-05-13T09:00:00Z",
    "is_current": true
  }
]
```

`is_current` flags the session corresponding to the Bearer token used in this request. **Note**: `is_current` is currently always `false` because `sid` is not yet propagated through the internal JWT.

**Related**: US-11.

---

### `DELETE /api/v1/auth/sessions/{session_id}`

Revokes a specific session. Users can only revoke their own sessions (enforced by `auth-service`).

Requires `Authorization: Bearer`.

**Path parameter**: `session_id` — UUID of the session to revoke.

**Response**: `204 No Content`.

**Audit event**: `auth.session.revoked.v1`.

---

### `POST /api/v1/auth/re-mfa`

Verifies the user's current TOTP code to unlock sensitive operations. Required before password change and admin actions.

Requires `Authorization: Bearer`.

**Request body** (SPA → BFF):
```json
{
  "code": "123456"
}
```

**BFF → auth-service translation**: calls `/auth/mfa-check` with `{"totp_code": "123456"}`.

**Response** (200):
```json
{
  "re_mfa_token": "<subject>:<jti>",
  "mfa_verified": true
}
```

`auth-service` sets a Redis flag (`mfa-verified:<user_id>`) with a short TTL. Subsequent admin or sensitive calls check this flag server-side. The `re_mfa_token` value returned by the BFF is an opaque string composed of the actor's `subject` and `jti`; the SPA passes it in subsequent admin call bodies as `re_mfa_token`.

**Error responses**:

| HTTP | Cause |
|---|---|
| `403` | Wrong TOTP code or MFA verification failed |
| `401` | Missing or invalid Bearer token |

---

### `POST /api/v1/auth/password/change`

Changes the authenticated user's password. Requires prior re-MFA.

Requires `Authorization: Bearer`.

**Request body** (SPA → BFF):
```json
{
  "current_password": "old-password",
  "new_password": "new-password-min-12-chars",
  "re_mfa_token": "<re_mfa_token from /re-mfa>"
}
```

**BFF → auth-service translation**: forwards only `new_password`. `current_password` is not forwarded — `auth-service` validates the current password from the stored argon2id hash. `re_mfa_token` is also not forwarded; the server-side Redis MFA flag is the gate.

**Response** (200):
```json
{"detail": "Password changed successfully"}
```

On the forced-enrollment path (user was forced to change password after admin reset), `auth-service` may return a new access + refresh pair. The BFF promotes the refresh to cookie and returns only the access token.

**Validation**: `new_password` min 12, max 1024 characters. HIBP top-1M check runs server-side; a breached password returns `400` with a localized hint.

**Related**: US-7, US-8. **Audit event**: `auth.user.password_changed.v1`.

---

## `/api/v1/admin/*` endpoints

All admin endpoints require:
1. `Authorization: Bearer <access_jwt>` with `role = "admin"`.
2. Re-MFA confirmation for write operations (indicated per endpoint).

The BFF enforces the admin role check before making any upstream call, returning `403` immediately for non-admins.

---

### `GET /api/v1/admin/users`

Lists all users. Read-only — no re-MFA required.

**Response** (200) — array of:
```json
[
  {
    "id": "01935f3c-a2b1-7000-8000-000000000001",
    "email": "alice@company.ru",
    "full_name": "Алиса Иванова",
    "role": "editor",
    "is_active": true,
    "must_change_password": false,
    "last_login_at": "2026-05-06T10:30:00Z",
    "created_at": "2026-05-01T08:00:00Z",
    "updated_at": "2026-05-06T10:30:00Z"
  }
]
```

**Related**: US-17.

---

### `GET /api/v1/admin/users/{user_id}`

Returns a single user by UUID. Read-only — no re-MFA required.

**Path parameter**: `user_id` — UUID.

**Response** (200): same shape as a single element from the list above.

**Error**: `404` if user not found.

---

### `POST /api/v1/admin/users`

Creates a new user. **Re-MFA required.**

**Request body**:
```json
{
  "email": "bob@company.ru",
  "full_name": "Боб Петров",
  "role": "viewer",
  "re_mfa_token": "<re_mfa_token>"
}
```

**Auth-service body** (BFF strips `re_mfa_token`, not forwarded):
```json
{
  "email": "bob@company.ru",
  "full_name": "Боб Петров",
  "role": "viewer"
}
```

`role` must be one of `"admin"`, `"editor"`, `"viewer"`.

**Response** (201):
```json
{
  "user_id": "01935f3c-a2b1-7f00-8012-aabbccddeeff",
  "oob_otp": "WXYZ-1234"
}
```

`oob_otp` is the one-time password the admin must relay to the new user out-of-band. It is shown once and not stored in plaintext. The OTP grants access only to the enrollment endpoints and has a 10-minute TTL.

**Error responses**:

| HTTP | Cause |
|---|---|
| `409` | Email already exists |
| `403` | Caller is not admin, or re-MFA flag not set |

**Related**: US-17. **Audit event**: `auth.user.created.v1`.

---

### `PATCH /api/v1/admin/users/{user_id}`

Partial update. Supports `role` change and deactivation (`active=false`). **Re-MFA required for both.**

The BFF fans out: if `role` is present it calls auth-service `PATCH .../role`; if `active=false` it calls `POST .../deactivate`.

**Request body**:
```json
{"role": "admin"}
```
or
```json
{"active": false}
```

**Response**: updated `UserResponse` (200) or `{"detail": "Updated"}` (204 path).

**Error**: `400` if neither `role` nor `active` is present in the body.

**Related**: US-18, US-19. **Audit events**: `auth.user.role_changed.v1`, `auth.user.deactivated.v1`.

---

### `POST /api/v1/admin/users/{user_id}/lockout`

Instant kill-switch: adds user to Redis `locked-users` SET and revokes all sessions. **Re-MFA required.**

**Request**: no body required by BFF (the upstream auth-service route is `204`).

**Response**: `204 No Content`.

The BFF checks `SISMEMBER locked-users <sub>` on every subsequent request for this user, returning `401` before any business logic runs. This provides sub-second revocation even before per-session Redis checks are implemented (see [ADR-0003 §13](../adr/0003-authentication-and-session-lifecycle.md)).

**Related**: US-13. **Audit event**: `auth.account.locked.v1` (severity=high).

---

### `DELETE /api/v1/admin/users/{user_id}/lockout`

Removes the Redis lockout flag (manual unlock). **Re-MFA required.**

**Response**: `204 No Content`.

Admin-set lockouts have no TTL — they require explicit unlock. This differs from the automatic 15-min/24-hr lockout triggered by failed login attempts.

---

### `GET /api/v1/admin/users/{user_id}/sessions`

Lists all active sessions for a target user. Read-only — no re-MFA required.

**Response** (200): array of `SessionResponse` (same shape as `/auth/sessions/me`).

**Related**: US-21.

---

### `DELETE /api/v1/admin/users/{user_id}/sessions`

Revokes all active sessions for a target user. **Re-MFA required.**

**Response**: `204 No Content`.

**Related**: US-15. **Audit event**: `auth.session.revoked.v1` (one per revoked session).

---

### `POST /api/v1/admin/users/{user_id}/totp/reset`

Resets a user's TOTP secret to the sentinel value, forcing re-enrollment on next login. The admin provides their own current TOTP code inline; the use case verifies it directly (does not use the Redis MFA flag).

**Request body**:
```json
{
  "admin_totp_code": "123456"
}
```

**Response**: `204 No Content` (BFF returns `{"detail": "TOTP reset"}` if auth-service returns empty 200).

What happens on reset:
1. `auth.users.totp_secret_enc` is set to the sentinel `b'\x00'`.
2. All active sessions for the target user are revoked.
3. All `auth.totp_backup_codes` rows for the user are deleted.

The target user must re-enroll TOTP on next login. The admin must issue a new OOB OTP separately via password reset if the user has no valid credentials.

**Error responses**:

| HTTP | Cause |
|---|---|
| `401` | Admin TOTP code is wrong |
| `403` | Caller is not admin |
| `404` | Target user not found |

**Related**: US-16. **Audit event**: `auth.user.totp_reset.v1`.

---

### `POST /api/v1/admin/users/{user_id}/password/reset`

Generates a new OOB OTP for a target user, sets `must_change_at_next_login=true`, revokes all sessions. **Re-MFA required** (Redis flag from `/re-mfa`).

**Request**: no body.

**Response** (200):
```json
{
  "oob_otp": "WXYZ-1234"
}
```

The admin relays the OTP to the user out-of-band. After logging in with the OTP, the user can only call `POST /api/v1/auth/password/change` — all other endpoints return `403 WWW-Authenticate: must_change_password` until the change is completed.

**Related**: US-20. **Audit event**: `auth.user.password_reset.v1`.

---

## Reference

- [ADR-0003 Authentication and Session Lifecycle](../adr/0003-authentication-and-session-lifecycle.md)

_Last updated: 2026-05-06_
