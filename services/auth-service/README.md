# auth-service

The root of identity for Лоцман. Owns accounts, passwords (argon2id), TOTP
enrollment/verification (pyotp), RS256 access-token issuance, opaque refresh
tokens, sessions, login-attempt tracking, and the manual key-rotation log.

Every other service trusts the `sub` and `role` claims in the internal JWT that
`web-bff` mints after validating an access token issued here. No service calls
`auth-service` directly except over the internal cross-service routes below.

🔐 **Roles (RBAC):** `super_admin` · `admin` · `editor` · `viewer`. The
`super_admin` role (migration `0006_super_admin_role`, ADR-0006) gates the
system panel; `admin` runs day-to-day user administration (ADR-0004,
two-tier administration). Role checks are exact-match: an `admin`-gated route
rejects a `super_admin` token and vice-versa.

---

## Owns

**Postgres schema**: `auth` (app role: `auth_app`). The `alembic_version` table
lives in the `auth` schema so it never collides with other services sharing the
Postgres instance.

**Tables** (SQLAlchemy models in `src/auth_service/db/models.py`):

| Table | Purpose |
|---|---|
| `users` | Accounts. Holds `role`, `is_active`, `must_change_password`, the encrypted TOTP secret (`totp_secret_enc`), and `ui_font_scale`. Soft-delete via `deleted_at`. |
| `sessions` | Refresh-token records (SHA-256 hash of the opaque token — never the token itself), with `expires_at` / `revoked_at`. |
| `login_attempts` | Per-email attempt log for rate-limiting and lockout. |
| `backup_codes` | Single-use, argon2id-hashed TOTP recovery codes — exactly 10 per user at enrollment. |
| `totp_used_codes` | Anti-replay: `(user_id, period_index)` of every accepted TOTP code. |
| `key_rotations` | Manual audit log of cryptographic key rotations (migration `0007`, ADR-0010). |
| `user_saved_filters` | Per-user named filter presets for the document registry grid (migration `0008`). |
| `outbox` / `outbox_dlq` | Transactional outbox and its dead-letter queue. |

`Role` is a domain value-object enum (`domain/value_objects.py`), not a table.
Refresh tokens are opaque and stored only as a hash on `sessions` — there is no
separate `RefreshToken` table. The TOTP secret is an encrypted column
(`users.totp_secret_enc`), not a separate `TotpSecret` table.

---

## Public surface

`auth-service` is internal-only. The browser never reaches it directly — all
traffic arrives from `web-bff` carrying an HS256 internal JWT in the
`X-Internal-Token` header (`aud=auth-service`). The full path of every route
below is prefixed with **`/api/v1`** (the v1 router is mounted at `/api/v1` in
`main.py`; the tables show the path relative to that prefix).

The **Min auth** column means:

- **internal JWT** — any valid internal-actor token.
- **actor** — a valid token bound to a specific user (`require_actor`).
- **admin** — token with `role == admin` (`require_role("admin")`).
- **super_admin** — token with `role == super_admin`.
- **+ re-MFA** — the operation also requires a fresh TOTP check. That check is
  enforced at the **BFF**, not here (see [Re-MFA model](#re-mfa-model)); the
  annotation marks which operations the BFF gates before forwarding.

### Health & telemetry

| Method | Path | Min auth | Description |
|---|---|---|---|
| GET | `/healthz` | — | Liveness probe |
| GET | `/readyz` | — | Readiness — checks Postgres and Redis |
| GET | `/metrics` | — | Prometheus metrics |

### `/api/v1/auth/*` — login & self-service auth flow

| Method | Path | Min auth | Description |
|---|---|---|---|
| POST | `/auth/login` | internal JWT | Step 1: email + password. Returns a TOTP `session_ticket` or, for users without TOTP, an `enrollment_token`. |
| POST | `/auth/totp/verify` | internal JWT | Step 2: TOTP code. Issues access JWT + opaque refresh token. |
| POST | `/auth/totp/enroll` | internal JWT (ticket) | Enrollment step 1 — returns `secret_b32` + `otpauth_url`. Identity comes from the enrollment ticket only. |
| POST | `/auth/totp/enroll/confirm` | internal JWT (ticket) | Enrollment step 2 — confirms the code, writes the encrypted secret, returns 10 backup codes. |
| POST | `/auth/change-password` | actor *or* ticket | Two lanes: forced first-login (enrollment ticket) or normal self-service. HIBP breach check applied. |
| POST | `/auth/backup-codes/regenerate` | actor | Regenerates all 10 backup codes (invalidates prior ones). |
| POST | `/auth/mfa-check` | actor | Verifies a TOTP code for re-MFA; sets the Redis `mfa-verified` flag. |
| POST | `/auth/refresh` | refresh cookie | Rotates the refresh token. Replay of a rotated token triggers chain-revoke. |
| POST | `/auth/logout` | actor | Revokes the current session and clears the refresh cookie. |
| GET | `/auth/sessions` | actor | Lists the actor's own active sessions. |
| DELETE | `/auth/sessions/{session_id}` | actor | Revokes one of the actor's own sessions. |

### `/api/v1/auth/me/*` — profile, email, saved filters

| Method | Path | Min auth | Description |
|---|---|---|---|
| GET | `/auth/me` | actor | Returns the authenticated user's profile, including `ui_font_scale`, `totp_enrolled`, and `is_locked`. |
| PATCH | `/auth/me` | actor | Updates `full_name` and/or `ui_font_scale` (80–150, percent of base; migration `0009`). |
| POST | `/auth/me/change-email/request` | actor (+ re-MFA) | Step 1 of self-service email change. Emails an 8-digit code to the new address; returns the masked new email. |
| POST | `/auth/me/change-email/confirm` | actor | Step 2 — confirms with the 8-digit code (the code itself is the second factor). |
| GET | `/auth/me/saved-filters` | actor | Lists the user's named filter presets (default first, then alphabetical). |
| POST | `/auth/me/saved-filters` | actor | Creates a preset (max 20 per user; name unique per user). |
| PATCH | `/auth/me/saved-filters/{filter_id}` | actor | Partially updates a preset. |
| DELETE | `/auth/me/saved-filters/{filter_id}` | actor | Deletes a preset. |

### `/api/v1/admin/*` — user administration (`role == admin`)

| Method | Path | Min auth | Description |
|---|---|---|---|
| POST | `/admin/users` | admin + re-MFA | Invites a user. `delivery=auto` (returns `channel_used`) or `delivery=show-otp` (returns the OOB OTP). |
| GET | `/admin/users` | admin | Lists all users. |
| GET | `/admin/users/{user_id}` | admin | Gets a single user. |
| PATCH | `/admin/users/{user_id}/role` | admin + re-MFA | Changes a user's role. |
| PATCH | `/admin/users/{user_id}/profile` | admin + re-MFA | Updates the target user's `full_name`. |
| POST | `/admin/users/{user_id}/deactivate` | admin + re-MFA | Soft-deactivates; revokes the user's sessions. |
| POST | `/admin/users/{user_id}/reactivate` | admin + re-MFA | Restores a soft-deactivated user. |
| DELETE | `/admin/users/{user_id}` | admin + re-MFA | Soft-deletes the user (hides the record and frees the email). |
| POST | `/admin/users/{user_id}/lockout` | admin + re-MFA | Instant kill-switch via Redis flag + session revocation. |
| DELETE | `/admin/users/{user_id}/lockout` | admin + re-MFA | Removes the Redis lockout flag. |
| GET | `/admin/users/{user_id}/sessions` | admin | Lists the target user's sessions. |
| DELETE | `/admin/users/{user_id}/sessions` | admin + re-MFA | Revokes all of the target user's sessions. |
| DELETE | `/admin/users/{user_id}/sessions/{session_id}` | admin + re-MFA | Revokes a single session of the target user. |
| POST | `/admin/users/{user_id}/totp/reset` | admin (inline re-MFA) | Resets the target's TOTP. Verifies the admin's own TOTP (`admin_totp_code`) inline rather than via the Redis flag. |
| POST | `/admin/users/{user_id}/password/reset` | admin + re-MFA | Resets the password; returns a new OOB OTP. |
| POST | `/admin/users/{user_id}/invite` | admin + re-MFA | Re-invites a pending user (invalidates the old OTP, issues a new one). |

### `/api/v1/system/*` — key-rotation log (`role == super_admin`)

| Method | Path | Min auth | Description |
|---|---|---|---|
| GET | `/system/keys` | super_admin | Returns the last rotation record per `key_id`. |
| POST | `/system/keys/{key_id}/rotated` | super_admin | Records a manual key rotation (`rotated_at`, optional `note`). Upserts by `key_id`. |

### `/api/v1/internal/*` — cross-service lookups

No role gate — any valid internal-actor JWT (`aud=auth-service`) is accepted;
the trust boundary is the internal network plus the signed token.

| Method | Path | Min auth | Description |
|---|---|---|---|
| POST | `/internal/users/lookup` | internal JWT | Bulk-lookup user names by IDs (max 100). Returns `{id: {id, full_name, email, is_active}}`. |
| GET | `/internal/users` | internal JWT | Lists users (`?active=true` for enabled accounts only). Returns `{id, email, full_name, is_active, role}`. |

OpenAPI: <http://localhost:8001/api/docs> (when running locally).

---

## Re-MFA model

Several admin and profile operations are marked **+ re-MFA**: they require the
actor to have passed a fresh TOTP check shortly beforehand. Per ADR-0004 the
**BFF is the sole MFA chokepoint** — it calls `/auth/mfa-check` (or verifies the
TOTP in the request body) and only forwards to `auth-service` once the actor is
re-verified. The `require_admin_re_mfa` dependency in `auth-service` is therefore
a pass-through that only re-confirms a valid admin actor.

Operations the BFF gates with re-MFA: invite / re-invite a user, change role,
update another user's profile, deactivate / reactivate / delete a user, manual
lockout and unlock, revoke another user's sessions, and reset a password. The
TOTP reset endpoint is the exception — it verifies the admin's own TOTP inline.

---

## Events published

Each event is written to the `auth.outbox` table in the **same transaction** as
the mutation that caused it, then forwarded to Redis Streams by the outbox
dispatcher (an ARQ cron task polling roughly once per second). Events use the
canonical envelope from `lotsman_shared.envelope`. Topics map to the Redis
Stream key — `auth.user`, `auth.session`, or `auth.account`.

Events consumed: **none** — `auth-service` is the root of identity.

### `auth.user`

| Event type | Severity | Trigger |
|---|---|---|
| `auth.user.created.v1` | info | Internal create path for a user |
| `auth.user.invited.v1` | info | Admin invites a user (`delivery=auto`/`show-otp`) |
| `auth.invitation.resent.v1` | info | OTP rotated for a TOTP-less user (re-invite / CLI re-bootstrap) |
| `auth.user.bootstrapped.v1` | info | First admin or super_admin bootstrapped via CLI |
| `auth.user.activated.v1` | info | Admin reactivates a user |
| `auth.user.deactivated.v1` | info | Admin deactivates a user |
| `auth.user.deleted.v1` | info | Admin soft-deletes a user |
| `auth.user.role_changed.v1` | info | Admin changes a user's role |
| `auth.user.profile_updated.v1` | info | User updates their own `full_name` |
| `auth.user.email_change_requested.v1` | info | User requests an email change (masked email only) |
| `auth.user.email_changed.v1` | info | Email change confirmed (full before/after) |
| `auth.user.password_changed.v1` | info | User changes their own password |
| `auth.user.password_reset.v1` | info | Admin resets another user's password |
| `auth.user.totp_enrolled.v1` | info | User completes TOTP enrollment |
| `auth.user.totp_reset.v1` | info | Admin resets another user's TOTP |
| `auth.user.backup_codes_regenerated.v1` | info | User regenerates backup codes |
| `auth.user.locked.v1` | info | Admin instant lockout (Redis flag) |
| `auth.user.filter_preset_saved.v1` | info | User saves a filter preset |
| `auth.user.filter_preset_updated.v1` | info | User updates a filter preset |
| `auth.user.filter_preset_deleted.v1` | info | User deletes a filter preset |

### `auth.session`

| Event type | Severity | Trigger |
|---|---|---|
| `auth.user.logged_in.v1` | info | Successful full login (TOTP or backup code) |
| `auth.session.revoked.v1` | info | Single session revoked (self, admin, or logout) |
| `auth.session.revoked_all.v1` | info | Admin revokes all of a user's sessions |
| `auth.session.rotated.v1` | info | Refresh token rotated on `/auth/refresh` |
| `auth.session.reuse_detected.v1` | **high** | A rotated refresh token was replayed; chain-revoke triggered |

### `auth.account`

| Event type | Severity | Trigger |
|---|---|---|
| `auth.account.locked.v1` | **high** | Failed-attempt threshold reached, or admin manual lockout |
| `auth.account.unlocked.v1` | info | Admin removes the lockout flag |
| `auth.policy.violation.v1` | **high** | An admin operation was blocked by a policy (e.g. `MIN_ADMINS`) |

> `auth.invitation.requested.v1` is **not** published here. The invite path emits
> `notification.invite.requested.v1` for the notification-service consumer; the
> plaintext OTP is delivered side-channel via Redis and never appears in the
> audit payload.

See [ADR-0002 §A. Ownership matrix](../../docs/adr/0002-service-boundaries.md#a-ownership-matrix).

---

## Configuration

Environment variables read at startup (`config.py`). Required unless a default
is shown.

| Variable | Default / example | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://auth_app:pw@localhost/lotsman` | Async SQLAlchemy DSN. **Required.** |
| `REDIS_URL` | `redis://localhost:6379/0` | Session store, jti replay cache, ARQ. |
| `INTERNAL_JWT_KEY_AUTH` | _(32+ random hex chars)_ | HS256 key for incoming internal JWTs (`aud=auth-service`). **Required**; startup fails if shorter than 32 chars. |
| `INTERNAL_JWT_KEY_NOTIFICATION` | _(empty)_ | HS256 key for minting JWTs to notification-service. Needed when an invite uses `delivery=auto` (channel lookup). Empty → channel reader returns no channels. |
| `NOTIFICATION_SVC_URL` | `http://notification-svc:8000` | Base URL of notification-service (used by invite and email-change flows). |
| `TOTP_ENC_KEY` | _(Fernet key, base64url)_ | Fernet master key encrypting `totp_secret_enc` at rest. **Required.** Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `JWT_PRIVATE_KEY_PATH` | `infra/secrets-dev/jwt_private_key.pem` | RS256 private key (PEM) for signing access JWTs. Mounted as a secret; never shared. |
| `JWT_PUBLIC_KEY_PATH` | `infra/secrets-dev/jwt_public_key.pem` | RS256 public key (PEM). Also mounted into `web-bff` for verification. |
| `JWT_CURRENT_KID` | `v1` | Key ID stamped into the access-token header. |
| `ACCESS_TOKEN_TTL_SECONDS` | `900` (15 min) | Access JWT TTL (bounds 60–3600). |
| `REFRESH_TOKEN_TTL_SECONDS` | `43200` (12 h) | Refresh-cookie/session TTL (bounds 3600–2592000; amended 2026-05-12). |
| `REFRESH_COOKIE_SECURE` | `true` | `Secure` flag on the refresh cookie. Set `false` only for local HTTP dev. |
| `REFRESH_COOKIE_SAMESITE` | `strict` | `SameSite` for the refresh cookie (`strict` / `lax` / `none`; `none` requires `secure=true`). |
| `OUTBOX_POLL_INTERVAL_SECONDS` | `1.0` | Outbox dispatcher poll interval. |

Generate the HS256 key for `INTERNAL_JWT_KEY_AUTH`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Local dev

Run standalone (Postgres and Redis must be up):

```bash
cd services/auth-service
DATABASE_URL=postgresql+asyncpg://auth_app:pw@localhost/lotsman \
REDIS_URL=redis://localhost:6379/0 \
INTERNAL_JWT_KEY_AUTH=dev00000000000000000000000000001 \
TOTP_ENC_KEY=<fernet-key> \
JWT_PRIVATE_KEY_PATH=./dev-keys/jwt_private.pem \
JWT_PUBLIC_KEY_PATH=./dev-keys/jwt_public.pem \
uv run uvicorn auth_service.main:app --reload --port 8001
```

Or via Docker Compose (starts all dependencies):

```bash
docker compose -f infra/compose.dev.yml up auth-svc --build
```

Port mapping: `127.0.0.1:8001 → container:8000`.

---

## Tests

```bash
# From repo root — auth-service tests only:
uv run pytest services/auth-service/tests -q

# Verbose:
uv run pytest services/auth-service/tests -v
```

The root `conftest.py` sets mock env vars so unit tests run without a real
database.

---

## Directory layout

```
services/auth-service/
├── alembic/                Alembic migration environment
│   └── versions/           Migration scripts (0001–0009)
├── alembic.ini             Alembic config (DSN read from env, not stored here)
├── src/auth_service/
│   ├── domain/             Entities, value objects, events, errors — no framework deps
│   ├── application/        Use cases, ports (Protocol), DTOs
│   ├── db/                 SQLAlchemy ORM models (source of truth for autogenerate)
│   ├── infrastructure/
│   │   ├── db/             Async session factory + repositories
│   │   ├── redis/          Redis-backed stores (sessions, lockout, tickets, OTP)
│   │   └── outbox/         Outbox dispatcher ARQ task
│   ├── api/v1/             FastAPI routers: auth, me, admin, system, internal
│   ├── scripts/            CLI bootstrap (admin / super_admin)
│   ├── config.py           Pydantic Settings (loaded from env)
│   └── main.py             App factory + lifespan (v1 router mounted at /api/v1)
├── tests/
├── Dockerfile
└── pyproject.toml
```

---

## Migrations

```bash
# New revision (run from this directory):
cd services/auth-service
uv run alembic revision --autogenerate -m "describe the change"

# Apply to local Postgres:
uv run alembic upgrade head

# Via make (applies migrations for all services):
make migrate
```

Nine revisions exist, `0001` → `0009`:

| Revision | Adds |
|---|---|
| `0001_initial_auth_schema` | Core schema: users, sessions, login_attempts, outbox |
| `0002_seed_system_actors` | System actor rows |
| `0003_add_totp_used_codes` | TOTP anti-replay table |
| `0004_add_backup_codes` | Single-use backup codes |
| `0005_lockout_partial_index` | Lockout partial index |
| `0006_super_admin_role` | `super_admin` in the role CHECK constraint (ADR-0006) |
| `0007_key_rotations` | `key_rotations` audit table (ADR-0010) |
| `0008_add_user_saved_filters` | `user_saved_filters` table |
| `0009_add_user_ui_font_scale` | `users.ui_font_scale` (80–150) |

The `alembic_version` table lives in the `auth` schema so it does not conflict
with other services sharing the same Postgres instance.

---

*Last updated: 2026-06-25 — super_admin & system panel, key-rotation log, saved
filters, self-service email change, invite/reactivate/delete, `ui_font_scale`.*
