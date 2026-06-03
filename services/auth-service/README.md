# auth-service

Handles user identity for Лоцман: account management, password verification (argon2id), TOTP enrollment and verification (pyotp), JWT issuance (RS256 access tokens), opaque refresh tokens stored in HttpOnly cookies, session lifecycle, and login-attempt tracking.

All other services trust the `sub` and `role` claims in the internal JWT that `web-bff` mints after validating the access token here. No other service calls `auth-service` directly.

---

## Owns

**Postgres schema**: `auth` (app role: `auth_app`)

**Domain entities**: `User`, `Role`, `TotpSecret`, `Session`, `RefreshToken`, `LoginAttempt`

---

## Public surface

`auth-service` is an internal service. The browser never calls it directly — all traffic comes from `web-bff` via an HS256 internal JWT. The paths below are internal paths (no `/api/v1` prefix).

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/healthz` | — | Liveness probe |
| GET | `/readyz` | — | Readiness — checks Postgres |
| GET | `/metrics` | — | Prometheus metrics |
| POST | `/auth/login` | Internal JWT | Step 1: email + password; returns session ticket or enrollment token |
| POST | `/auth/totp/verify` | Internal JWT | Step 2: TOTP code; issues access JWT + opaque refresh |
| POST | `/auth/refresh` | Internal JWT + cookie | Rotates refresh token; reuse triggers chain-revoke |
| POST | `/auth/logout` | Internal JWT | Revokes current session; clears cookie |
| POST | `/auth/totp/enroll` | Internal JWT (actor) | Step 1 of enrollment — returns `secret_b32` + `otpauth_url` |
| POST | `/auth/totp/enroll/confirm` | Internal JWT (actor) | Step 2 — confirms code; writes encrypted secret; returns 10 backup codes |
| POST | `/auth/backup-codes/regenerate` | Internal JWT (actor) | Regenerates all 10 backup codes (invalidates prior ones) |
| POST | `/auth/mfa-check` | Internal JWT (actor) | Verifies TOTP for re-MFA; sets Redis `mfa-verified` flag |
| GET | `/auth/sessions` | Internal JWT (actor) | Lists actor's active sessions |
| DELETE | `/auth/sessions/{session_id}` | Internal JWT (actor) | Revokes a specific session (self-only) |
| POST | `/auth/change-password` | Internal JWT (actor) | Changes password; HIBP check; requires prior re-MFA |
| POST | `/admin/users` | Internal JWT (admin, re-MFA) | Creates user; returns OOB OTP |
| GET | `/admin/users` | Internal JWT (admin) | Lists all users |
| GET | `/admin/users/{user_id}` | Internal JWT (admin) | Gets single user |
| PATCH | `/admin/users/{user_id}/role` | Internal JWT (admin, re-MFA) | Changes role |
| POST | `/admin/users/{user_id}/deactivate` | Internal JWT (admin, re-MFA) | Soft-deactivates user; revokes sessions |
| POST | `/admin/users/{user_id}/lockout` | Internal JWT (admin, re-MFA) | Instant kill-switch via Redis + session revocation |
| DELETE | `/admin/users/{user_id}/lockout` | Internal JWT (admin, re-MFA) | Removes Redis lockout flag |
| GET | `/admin/users/{user_id}/sessions` | Internal JWT (admin) | Lists target user's sessions |
| DELETE | `/admin/users/{user_id}/sessions` | Internal JWT (admin, re-MFA) | Revokes all sessions of target user |
| DELETE | `/admin/users/{user_id}/sessions/{session_id}` | Internal JWT (admin, re-MFA) | Revokes a single session of target user |
| POST | `/admin/users/{user_id}/totp/reset` | Internal JWT (admin) | Resets TOTP (inline re-MFA via `admin_totp_code`) |
| POST | `/admin/users/{user_id}/password/reset` | Internal JWT (admin, re-MFA) | Resets password; returns new OOB OTP |

OpenAPI: http://localhost:8001/api/docs (when running locally).

---

## Events published

All events are written to the `auth.outbox` table in the same DB transaction, then dispatched by the outbox-dispatcher ARQ worker.

| Event type | Severity | Trigger |
|---|---|---|
| `auth.user.created.v1` | info | Admin creates a user |
| `auth.user.deactivated.v1` | info | Admin deactivates a user |
| `auth.user.role_changed.v1` | info | Admin changes a user's role |
| `auth.user.logged_in.v1` | info | Successful full login (TOTP verified) |
| `auth.user.logged_out.v1` | info | User or admin calls logout |
| `auth.user.password_changed.v1` | info | User changes their own password |
| `auth.user.password_reset.v1` | info | Admin resets another user's password |
| `auth.user.totp_reset.v1` | info | Admin resets another user's TOTP |
| `auth.session.revoked.v1` | info | Single session revoked (self or admin) |
| `auth.session.reuse_detected.v1` | **high** | Rotated refresh token replayed; chain-revoke triggered |
| `auth.account.locked.v1` | **high** | 10 failed attempts in 60 min; or admin manual lockout |

Events consumed: **none** — `auth-service` is the root of identity.

All events use the canonical envelope from `lotsman_shared.envelope`. See [ADR-0002 §A](../../docs/adr/0002-service-boundaries.md).

---

## Configuration

Environment variables read by `auth-service` at startup. All are required unless marked optional.

| Variable | Example | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://auth_app:pw@localhost/lotsman` | Async SQLAlchemy DSN |
| `REDIS_URL` | `redis://localhost:6379/0` | Session store + jti replay cache |
| `INTERNAL_JWT_KEY_AUTH` | _(32+ random hex chars)_ | HS256 key for incoming internal JWTs (`aud=auth-service`). **Replaces the former `INTERNAL_JWT_SECRET`.** Startup fails if shorter than 32 chars. |
| `TOTP_ENC_KEY` | _(Fernet key, base64url)_ | Fernet master key for encrypting `totp_secret_enc` at rest. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `JWT_PRIVATE_KEY_PATH` | `/run/secrets/jwt_private.pem` | RS256 private key (PEM, file mode `0400`). Mounted as Docker secret; never shared with other services. |
| `JWT_PUBLIC_KEY_PATH` | `/run/secrets/jwt_public.pem` | RS256 public key (PEM). Also mounted into `web-bff` for JWT verification. |

Generate a 32-char key for `INTERNAL_JWT_KEY_AUTH`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

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
# From repo root — runs auth-service tests only:
uv run pytest services/auth-service/tests -q

# With verbose output:
uv run pytest services/auth-service/tests -v
```

The root `conftest.py` sets mock env vars so unit tests run without a real database.

---

## Directory layout

```
services/auth-service/
├── alembic/                Alembic migration environment
│   └── versions/           Migration scripts (0001_initial_auth_schema.py, …)
├── alembic.ini             Alembic config (DSN is NOT stored here — read from env)
├── src/auth_service/
│   ├── domain/             Entities, value objects, domain errors — no external deps
│   ├── application/        Use cases, ports (Protocol), DTOs
│   ├── infrastructure/
│   │   ├── db/             SQLAlchemy models, async session factory
│   │   └── outbox/         Outbox dispatcher ARQ worker
│   ├── api/
│   │   └── v1/             FastAPI routers (feature routers added here)
│   ├── config.py           Pydantic Settings (loaded from env)
│   └── main.py             App factory + lifespan
├── tests/
│   └── unit/               Unit tests (no DB required)
├── Dockerfile
└── pyproject.toml
```

---

## Migrations

```bash
# Create a new revision (run from this directory):
cd services/auth-service
uv run alembic revision --autogenerate -m "describe the change"

# Apply to local Postgres:
uv run alembic upgrade head

# Via make (applies all four services):
make migrate
```

The `alembic_version` table is stored in the `auth` schema so it does not conflict with other services sharing the same Postgres instance.

---

*Last updated: 2026-05-06 — updated for the auth feature (endpoint list, env vars, events)*
