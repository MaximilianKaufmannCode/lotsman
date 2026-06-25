# Dev secrets — how to generate them

This directory is gitignored (see `infra/secrets-dev/*` in `.gitignore`).
It holds ONLY development keypairs and keys generated locally.
Do NOT put any production secrets here.

The production equivalents are managed via Docker secrets or a secrets manager
(pass, Vault, etc.). The rotation procedure is not yet documented — it is a
tracked ops follow-up (ADR-0003 §Implementation handoff). Rotation cadences are
noted per-secret below.

---

## 1. RS256 keypair for external JWT

auth-service signs access tokens with the private key.
All other services verify with the public key.

The filenames matter: `compose.dev.yml` mounts `./secrets-dev/jwt-private.pem`
and `./secrets-dev/jwt-public.pem` (hyphenated) into the containers. Generate
them with exactly these names:

```bash
# Generate 2048-bit RSA private key (PEM, PKCS#8)
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
    -out infra/secrets-dev/jwt-private.pem

# Derive the public key
openssl pkey -in infra/secrets-dev/jwt-private.pem \
    -pubout -out infra/secrets-dev/jwt-public.pem
```

You do NOT set the key paths in `.env` for the compose stack: `compose.dev.yml`
mounts both files into `/run/secrets/` and already points the services at them
via `JWT_PRIVATE_KEY_PATH=/run/secrets/jwt-private.pem` and
`JWT_PUBLIC_KEY_PATH=/run/secrets/jwt-public.pem`. Just generate the files at the
names above and `make dev` wires the rest.

> Heads-up: `.env.example` currently carries stale values
> (`/run/secrets/jwt_private_key`, underscore + no `.pem`) that do not match the
> compose mount points. Ignore them for the compose stack; they are pending
> sync with `compose.dev.yml`.

Rotation cadence: every 90 days (manual; verifier tolerates the previous public
key for one TTL window via `kid`). The step-by-step procedure is an open ops
follow-up (ADR-0003 §Implementation handoff).

---

## 2. HS256 per-service keys for internal JWT (web-bff <-> backends)

Per ADR-0003 §10 (F-001, F-002). 60-second TTL. web-bff signs with a per-target key;
each backend service verifies only its own key. One key per downstream — generate
one here for each of the four backends below, plus a fifth for the system-control
sidecar (see §6). Every `INTERNAL_JWT_KEY_*` value MUST be distinct; web-bff
asserts uniqueness across all of them at startup (R-5g).

```bash
python -c "import secrets; print(secrets.token_hex(32))"  # run once per key
```

Copy each output into `.env` as a separate variable:

```
INTERNAL_JWT_KEY_AUTH=<first_output>
INTERNAL_JWT_KEY_REGISTRY=<second_output>
INTERNAL_JWT_KEY_NOTIFICATION=<third_output>
INTERNAL_JWT_KEY_AUDIT=<fourth_output>
```

IMPORTANT: never reuse values across keys — including `INTERNAL_JWT_KEY_SYSTEM_CONTROL`
from §6.

---

## 3. Fernet key for TOTP secret encryption

TOTP seeds are stored encrypted at rest in `auth.users` (or a related table).
The Fernet key is the master encryption key for those values.

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output into `.env` as `TOTP_ENC_KEY`.

Store this key separately from the database. If the database is stolen but
the key is safe, TOTP secrets cannot be decrypted.

---

## 4. Per-service Postgres role passwords

The four application roles (`auth_app`, `registry_app`, `notification_app`,
`audit_app`) are created as LOGIN roles without a password by
`infra/postgres/init/01-schemas-and-roles.sql`. Their passwords are set
automatically on the **container's first start** by
`infra/postgres/init/03-set-role-passwords.sh`, which runs as the postgres
superuser from `/docker-entrypoint-initdb.d/` and reads them from the
environment:

```
AUTH_PG_PASSWORD
REGISTRY_PG_PASSWORD
NOTIFICATION_PG_PASSWORD
AUDIT_PG_PASSWORD
```

So for a fresh volume you only need to set those four `*_PG_PASSWORD` variables
(and the matching `*_DATABASE_URL`) in `.env` before `make dev`; the init script
issues the `ALTER ROLE ... PASSWORD ...` calls for you. If a variable is unset or
empty, that role is left without a password — the dev default, acceptable where
Postgres is only reachable on `127.0.0.1`.

Manual fallback — only when the init script has already run (an existing volume)
or when rotating a password, set it by hand:

```bash
docker exec -it lotsman_postgres psql -U postgres -c "
  ALTER ROLE auth_app         PASSWORD '${AUTH_PG_PASSWORD}';
  ALTER ROLE registry_app     PASSWORD '${REGISTRY_PG_PASSWORD}';
  ALTER ROLE notification_app PASSWORD '${NOTIFICATION_PG_PASSWORD}';
  ALTER ROLE audit_app        PASSWORD '${AUDIT_PG_PASSWORD}';
"
```

Under Podman (PreProd runs Podman), swap the runtime: `podman exec -it lotsman_postgres psql ...`.

Then update the corresponding `*_DATABASE_URL` and `*_PG_PASSWORD` variables
in your `.env` file (if changed) and restart the affected services:

```bash
docker compose -f infra/compose.dev.yml restart auth-svc registry-svc notification-svc audit-svc
# Podman: podman compose -f infra/compose.dev.yml restart auth-svc registry-svc notification-svc audit-svc
```

---

## 5. Fernet key for channel config encryption

notification-service stores SMTP/Telegram/Dion credentials encrypted at rest in
`notification.provider_credentials.config_enc` (BYTEA).  This key encrypts those blobs.

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output into `.env` as `CHANNEL_ENC_KEY`.

**MUST be distinct from `TOTP_ENC_KEY`** — using the same key would conflate two separate
encryption domains (ADR-0004 §4).

notification-service refuses to boot when `CHANNEL_ENC_KEY` is absent or empty.

---

## 6. HS256 key for system-control sidecar (super-admin panel)

The `system-control` sidecar authenticates every request via an internal JWT
with `aud="system-control"`.  web-bff mints these tokens using
`INTERNAL_JWT_KEY_SYSTEM_CONTROL`.

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output into `.env` as `INTERNAL_JWT_KEY_SYSTEM_CONTROL`.

**Rules**:
- Must be **distinct** from all other `INTERNAL_JWT_KEY_*` values (F-002).
- web-bff asserts uniqueness at startup.
- If this key is absent, the web-bff starts normally but all sidecar-backed
  `/api/v1/system/*` endpoints return 503. The rest of the application
  continues to function.
- The same key must be set in both web-bff AND system-control environment.

**Docker GID** (for `user:` in compose.dev.yml):

```bash
stat -c '%g' /var/run/docker.sock
# Add result as DOCKER_GID=<value> in .env, or override inline:
# user: "1001:<your_gid>"
```

Common values: `999` (Debian/Ubuntu), `986` (Arch Linux), `0` (rootless Docker).
For rootless Podman, the socket is typically owned by your user (no group needed).

---

## 7. Grafana admin password (observability stack)

Only relevant if you run `compose.observability.yml`. Set
`GRAFANA_ADMIN_PASSWORD` in `.env` (consumed as `GF_SECURITY_ADMIN_PASSWORD`);
`.env.example` ships a placeholder. No file is generated in this directory — it
is a plain `.env` value.

---

## What NOT to put in this directory

- Production private keys
- Production database passwords
- Any file containing real credentials used in a live environment

If you accidentally commit a secret, invalidate it immediately and rotate it.
Then use `git filter-repo` to purge the commit history.
