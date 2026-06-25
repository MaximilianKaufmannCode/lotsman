# system-control

Privileged operations sidecar for Лоцман. It performs a small, fixed set of
host-level Docker operations — restart a service, run a backup, apply
migrations, tail logs, list containers — on behalf of a **super-admin** acting
through the System panel in web-bff.

> **Rule:** this service is internal-only. It listens **only** on the
> `lotsman-internal` network, has **no host-port mapping** (unlike every other
> service), and mounts the Docker socket. Never expose it to the internet, and
> never call it from anything other than web-bff with a valid internal JWT.

It is deliberately tiny and locked down: every operation is gated by an internal
JWT (`aud=system-control`, `role=super_admin`), and every target is checked
against a **hardcoded allow-list** of five containers. See
[ADR-0009 — system-control privilege reduction](../../docs/adr/0009-system-control-privilege-reduction.md)
and [ADR-0006 — super-admin role & system panel](../../docs/adr/0006-super-admin-role-and-system-panel.md).

`v0.1.0` · BUSL-1.1

## Owns

- **No PostgreSQL schema, no Alembic migrations, no domain entities** — this is a
  stateless control-plane sidecar, not a domain service.
- **No Redis, no outbox, no event consumers** — synchronous HTTP only.
- The only "data" it owns is a **module-level allow-list** of services and
  operations (`domain/whitelist.py`), defined as constants in code.

## Public surface

### System

| Method · Path | Purpose |
|---|---|
| `GET /healthz` | Liveness — always `200` if the process is up. |
| `GET /readyz` | Readiness — checks the Docker socket is reachable. |
| `GET /metrics` | Prometheus metrics. |

### `/v1/*` — Privileged operations (internal-only)

Every `/v1/*` request **requires a valid internal JWT** (see [Auth](#auth)) and a
target that is on the allow-list. Operation **stdout/output is never logged**
(only tailed into the response) so backup paths, DSNs, and other secrets can't
leak into the log stream.

| Method · Path | Body / Query | Returns |
|---|---|---|
| `POST /v1/restart-service` | `{ "service": "<name>" }` | `{ exit_code, duration_ms }` |
| `POST /v1/backup-now` | `{}` | `{ exit_code, stdout_tail, duration_ms }` — last 20 lines of stdout; timeout 600 s |
| `POST /v1/migrate` | `{ "service": "<name>" }` | `{ exit_code, output_tail, duration_ms }` — last 50 lines; runs `alembic upgrade head` |
| `GET /v1/logs` | `?service=<name>&tail=<1..500>` (default 100) | `{ lines, truncated }` |
| `GET /v1/ps` | — | `[ { name, status, uptime, image } ]` |

`<name>` is a short service name (`auth-svc`, `registry-svc`, `notification-svc`,
`audit-svc`, `web-bff`), resolved to its container by the allow-list. An unknown
name is rejected with `400` before any Docker call.

## Auth

The single gate is an **internal JWT** in the `X-Internal-Token` header, minted
by web-bff for a super-admin action. There is **no fallback auth, no basic-auth**
— any deviation returns `401`.

| Claim / property | Required value |
|---|---|
| `alg` | `HS256` |
| `aud` | `system-control` |
| `iss` | `web-bff` |
| `role` | `super_admin` |
| TTL | ~60 s (leeway 2 s) |
| Required claims | `exp`, `iat`, `nbf`, `sub`, `aud`, `iss`, `jti`, `role` |

The verification key is `INTERNAL_JWT_KEY_SYSTEM_CONTROL` — a **per-service** key,
separate from every other internal key (see
[ADR-0003 §10 — per-service internal JWT keys](../../docs/adr/0003-authentication-and-session-lifecycle.md)).
It must match the same-named key configured in [web-bff](../web-bff/README.md),
which mints the token.

## Allow-list & hardening

- **Five containers only** (`domain/whitelist.py`): `lotsman_auth_svc`,
  `lotsman_registry_svc`, `lotsman_notification_svc`, `lotsman_audit_svc`,
  `lotsman_web_bff`. The set is a module-level `frozenset` — never populated from
  user input, env, or the database. Adding a service is a code change + review.
- The Alembic command is **hardcoded** (`("alembic", "upgrade", "head")`,
  workdir `/app`) — never interpolated from input.
- Log tail is capped at `MAX_LOG_TAIL = 500` (DoS guard); backup runs with a
  600 s timeout.
- Docker is driven through the Docker SDK (`docker.from_env()`), not a shell;
  the backup is the only `subprocess.run([...])` call, with a list argv and a
  path from settings — never user input.

## Events published / consumed

**None.** This sidecar does not read or write Redis Streams.

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `INTERNAL_JWT_KEY_SYSTEM_CONTROL` | — (required, min 32 chars) | Must match the same key in web-bff; the service won't start without it. |
| `SERVICE_NAME` | `system-control` | |
| `LOG_LEVEL` | `info` | |
| `BACKUP_SCRIPT_PATH` | `/scripts/backup.sh` | Mounted read-only; invoked by `backup-now`. |
| `DOCKER_LABEL_PREFIX` | `com.docker.compose.project=lotsman` | Scopes `ps`/`logs` to the project. |

Generate a key: `python -c "import secrets; print(secrets.token_hex(32))"`.

## Local dev

system-control is defined **only** in `infra/compose.dev.yml` and starts with
**no host port** — reach it via internal DNS at `http://system-control:8000`.

```bash
docker compose -f infra/compose.dev.yml up system-control --build
```

It mounts `/var/run/docker.sock` and runs as `user: "1001:${DOCKER_GID}"`, where
`DOCKER_GID` is the GID that owns the socket on the host:

```bash
stat -c '%g' /var/run/docker.sock   # export DOCKER_GID=<result>
```

Under rootless Docker / Podman the socket path and GID differ — point the mount
at the rootless socket (e.g. `$XDG_RUNTIME_DIR/docker.sock`) accordingly.

> **Production note.** system-control is **not** part of `infra/compose.prod.yml`
> — there is no privileged sidecar in production. Also note a known divergence
> from [ADR-0009](../../docs/adr/0009-system-control-privilege-reduction.md): the
> `docker-socket-proxy` described there is still *Proposed* and is **not** yet in
> the Compose files — the socket is currently mounted directly. Treat the dev
> blast-radius accordingly.

## Tests

```bash
uv run pytest services/system-control/tests -q
```

Tests do **not** need a Docker socket — the app-factory test mocks `Settings`,
and the auth/allow-list tests are pure unit tests. Note the test directory is
flat (`tests/`), not `tests/unit/`.

## Migrations

None — no database, no Alembic. See [Owns](#owns).

## Directory layout

```
services/system-control/
├── src/system_control/
│   ├── domain/whitelist.py        # allow-listed services + ops (constants)
│   ├── api/v1/
│   │   ├── docker_ops.py          # POST /v1/restart-service
│   │   ├── backup_ops.py          # POST /v1/backup-now
│   │   ├── migrate_ops.py         # POST /v1/migrate
│   │   ├── logs.py                # GET  /v1/logs
│   │   └── ps.py                  # GET  /v1/ps
│   ├── auth.py                    # internal-JWT gate (X-Internal-Token)
│   ├── config.py                  # settings / env
│   └── main.py                    # app factory, health + metrics routers
└── tests/                         # test_app_factory · test_auth · test_whitelist
```

There are intentionally **no `application/` or `infrastructure/` layers** — the
service is small enough that domain + api + auth + config is the whole of it.

---

*Last updated: 2026-06-25*
