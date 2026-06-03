# Dev secrets — how to generate them

This directory is gitignored (see `infra/secrets-dev/*` in `.gitignore`).
It holds ONLY development keypairs and keys generated locally.
Do NOT put any production secrets here.

The production equivalents are managed via Docker secrets or a secrets manager
(pass, Vault, etc.). See `docs/architecture/secrets.md` for the rotation procedure.

---

## 1. RS256 keypair for external JWT

auth-service signs access tokens with the private key.
All other services verify with the public key.

```bash
# Generate 2048-bit RSA private key (PEM, PKCS#8)
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
    -out infra/secrets-dev/jwt_private_key.pem

# Derive the public key
openssl pkey -in infra/secrets-dev/jwt_private_key.pem \
    -pubout -out infra/secrets-dev/jwt_public_key.pem
```

Then set in `.env`:
```
JWT_PRIVATE_KEY_PATH=/path/to/infra/secrets-dev/jwt_private_key.pem
JWT_PUBLIC_KEY_PATH=/path/to/infra/secrets-dev/jwt_public_key.pem
```

Rotate every 90 days. Document rotation in `docs/architecture/secrets.md`.

---

## 2. HS256 per-service keys for internal JWT (web-bff <-> backends)

Per ADR-0003 §10 (F-001, F-002). 60-second TTL. web-bff signs with a per-target key;
each backend service verifies only its own key. All four keys MUST be distinct — the
web-bff asserts uniqueness at startup (R-5g).

```bash
python -c "import secrets; print(secrets.token_hex(32))"  # run 4 times
```

Copy each output into `.env` as a separate variable:

```
INTERNAL_JWT_KEY_AUTH=<first_output>
INTERNAL_JWT_KEY_REGISTRY=<second_output>
INTERNAL_JWT_KEY_NOTIFICATION=<third_output>
INTERNAL_JWT_KEY_AUDIT=<fourth_output>
```

IMPORTANT: never reuse values across these four keys.

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

After `make dev` starts the Postgres container, set the role passwords
(they are created as LOGIN roles without a password by `01-schemas-and-roles.sql`):

```bash
docker exec -it lotsman_postgres psql -U postgres -c "
  ALTER ROLE auth_app         PASSWORD 'your_auth_password';
  ALTER ROLE registry_app     PASSWORD 'your_registry_password';
  ALTER ROLE notification_app PASSWORD 'your_notification_password';
  ALTER ROLE audit_app        PASSWORD 'your_audit_password';
"
```

Then update the corresponding `*_DATABASE_URL` and `*_PG_PASSWORD` variables
in your `.env` file and restart the affected services:

```bash
docker compose -f infra/compose.dev.yml restart auth-svc registry-svc notification-svc audit-svc
```

---

---

## 5. Fernet key for channel config encryption

notification-service stores SMTP/Telegram/Dion credentials encrypted at rest in
`notification.provider_credentials.config_enc` (BYTEA).  This key encrypts those blobs.

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output into `.env` as `CHANNEL_ENC_KEY`.

**MUST be distinct from `TOTP_ENC_KEY`** — using the same key would conflate two separate
encryption domains (ADR-0004 §4 / F-005 admin-channels-review).

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

## What NOT to put in this directory

- Production private keys
- Production database passwords
- Any file containing real credentials used in a live environment

If you accidentally commit a secret, invalidate it immediately and rotate it.
Then use `git filter-repo` to purge the commit history.
