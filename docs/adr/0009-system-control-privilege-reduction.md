# ADR-0009: `system-control` Privilege Reduction — Docker-Socket-Proxy or Path-Replacement

- **Status**: Proposed
- **Date**: 2026-05-22
- **Deciders**: architect (proposed), venawaziwoco83@gmail.com (pending)
- **Depends on**: ADR-0001 (Tech stack — Docker Compose on-prem), ADR-0006 (Super-admin role & system panel — defines what `system-control` exposes)
- **References**:
  - `services/system-control/src/system_control/main.py` (FastAPI app, container restart endpoints)
  - `infra/compose.prod.yml` (lines ~425–440: `system-control` block — `volumes: - /var/run/docker.sock:/var/run/docker.sock:rw`)

## Context

`system-control` is an internal FastAPI sidecar that lets the **super-admin** UI restart individual services from the Лоцман admin panel (ADR-0006 §5: «Recovery actions» — restart auth-svc, restart notification worker, drain queue, etc.). It runs as `network_mode: host`, listens on `127.0.0.1:8005`, and is **not** exposed by nginx — only `web-bff` can reach it (intra-host loopback).

To restart sibling containers, `system-control` currently mounts the **Docker compatibility socket** read-write into its container:

```yaml
# infra/compose.prod.yml
system-control:
  user: "1001:${DOCKER_GID:-999}"
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:rw
```

Inside the container, the Python code uses the Docker SDK to call `client.containers.get("lotsman_auth_svc").restart()` etc.

### Why this is a problem

Mounting `docker.sock` RW into any container — regardless of the container's UID — gives that container **root-equivalent** on the host. The Docker/Docker API exposes:

- `POST /containers/create` with `--privileged: true`, `Binds: /:/host`, `CapAdd: [SYS_ADMIN]` — i.e. mint a new privileged container that mounts the host root filesystem and immediately drops to host root.
- `POST /containers/<id>/exec` — run arbitrary commands inside any other container.
- `POST /containers/<id>/copy` — read/write any file in any container's filesystem.
- Manage networks, volumes, images.

The mitigation that exists today — `user: "1001:DOCKER_GID"` — only changes the UID of the Python process **inside `system-control`**; it does not constrain what the Docker API accepts from anyone holding the socket. The full attack surface of the local Docker daemon is exposed to any code path that runs inside `system-control`.

### Threat model

- **T1 (primary):** RCE / SSRF in `system-control` or any sibling that can reach it (e.g. `web-bff` if abused). One attacker-controlled `client.containers.run(image="busybox", privileged=True, volumes={"/":{...}})` → host root.
- **T2:** Supply-chain compromise of the Docker SDK Python library — same outcome.
- **T3:** Insider risk — any developer who can deploy to production already has full host SSH, so insider-via-`system-control` is not strictly worse than insider-via-SSH; this threat is **outside** the model.

T1 and T2 are real. The mitigation must reduce what `system-control` can do via `docker.sock` to *exactly* the set of operations the admin panel needs.

### Operations actually used

Per `services/system-control/src/system_control/`, the sidecar uses **a single API verb** per use case:

| Use case | API verb |
|---|---|
| Restart a service | `POST /containers/<name>/restart` |
| (read) get container status | `GET /containers/<name>/json` (read-only inspect) |
| (read) list containers for the panel | `GET /containers/json?filters=...` |

No `create`, no `exec`, no `copy`, no `start`/`stop`/`kill` outside of `restart`. No images, no networks, no volumes. The actual privilege requirement is **tiny**.

## Decision

**Adopt Option A — `docker-socket-proxy` reverse-proxy with a narrow allow-list (RFC 2119 normative).**

`system-control` will continue to think it talks to `docker.sock`, but the socket inside the container will be a Unix-domain socket served by a **separate, hardened sidecar** that proxies only the three allowed verbs to the real `/var/run/docker.sock`. The real socket is **never** mounted into `system-control`.

### Topology

```
[ super-admin UI ]
        │  HTTPS
        ▼
[ nginx ] ──► [ web-bff:8080 ] ──► [ system-control:8005 ]
                                          │
                                          │ Unix socket /var/run/docker.sock
                                          ▼
                                  [ docker-socket-proxy ]    ← new sidecar
                                          │
                                          │ Unix socket /var/run/docker.sock
                                          ▼
                                  [ Docker socket on host ]
```

Both sides of the proxy use `/var/run/docker.sock` as the path — `system-control` does NOT need code changes. Only the bind-mount in compose changes.

### Allow-list inside `docker-socket-proxy`

Implement as **Tecnativa/docker-socket-proxy** (or a hand-rolled minimal Python/Go proxy if Tecnativa is not acceptable as an external image — see §Alternatives). Environment variables that gate the API surface, in normative form:

```yaml
docker-socket-proxy:
  image: docker.io/tecnativa/docker-socket-proxy:0.3.0
  network_mode: host                          # same loopback the rest of the stack uses
  user: "${DOCKER_GID:-999}:${DOCKER_GID:-999}"
  read_only: true
  cap_drop: [ALL]
  cap_add: [CHOWN, DAC_OVERRIDE]              # only what proxy needs to open socket
  environment:
    CONTAINERS: 1                             # GET /containers/json, /containers/<id>/json
    POST: 1                                   # enable POST so /restart works at all
    ALLOW_RESTARTS: 1                         # POST /containers/<id>/restart  ← single allowed verb
    # explicit denies (defence-in-depth; Tecnativa default-denies, but be explicit):
    EXEC: 0
    BUILD: 0
    COMMIT: 0
    CONFIGS: 0
    DISTRIBUTION: 0
    EVENTS: 0
    IMAGES: 0
    INFO: 0
    NETWORKS: 0
    NODES: 0
    PLUGINS: 0
    SECRETS: 0
    SERVICES: 0
    SESSION: 0
    SWARM: 0
    SYSTEM: 0
    TASKS: 0
    VOLUMES: 0
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro    # READ-only mount of REAL socket
  restart: unless-stopped
```

Then in `system-control` change the mount:

```yaml
system-control:
  user: "1001:${DOCKER_GID:-999}"
  volumes:
    - /var/run/docker.sock-proxy:/var/run/docker.sock:rw   # proxy socket, not real
  depends_on:
    - docker-socket-proxy
```

(`docker-socket-proxy` writes its outbound Unix socket to `/var/run/docker.sock-proxy` on the host, which `system-control` mounts as if it were the real socket.)

### Normative requirements

**[NR-1]** `system-control` MUST NOT mount the host's real `/var/run/docker.sock`. The mount path must resolve to the proxy's socket only. (Verification: `Docker inspect lotsman_system_control --format '{{json .Mounts}}'` shows no `/var/run/docker.sock:ro` from host.)

**[NR-2]** The proxy MUST allow only: `GET /containers/json`, `GET /containers/<id>/json`, `POST /containers/<id>/restart`. Any other verb MUST return 403. (Verification: contract test in `tests/integration/test_socket_proxy_allowlist.py` exercising 20+ denied verbs.)

**[NR-3]** The proxy container itself MUST be `read_only: true`, `cap_drop: [ALL]`, with only the minimum `cap_add` required to open the socket — never `cap_add: NET_ADMIN`, `SYS_ADMIN`, etc.

**[NR-4]** The proxy MUST NOT have a host-network port mapping. It exposes only the Unix socket on the host filesystem (also mounted into `system-control`). Verification: `ss -tlnp | grep docker-socket-proxy` returns empty.

**[NR-5]** `docker-socket-proxy` logs MUST be shipped to the same observability stack as other lotsman containers (per ADR-0011 once observability lands). Every denied verb should be logged with `actor=<container>` for audit. (This is forward-looking; if observability isn't yet up at implementation time, at minimum `Docker logs lotsman_docker_socket_proxy` must show denials.)

**[NR-6]** If the proxy container is down, `system-control` operations MUST fail closed (HTTP 503 from `system-control` API). Adding a smoke health-check at startup that pings the proxy is sufficient.

**[NR-7]** No production code change to `system-control` itself is required (and none is desired in this ADR). The Docker SDK Python library talks to `/var/run/docker.sock` exactly as before; only the socket on the other end of that path changed.

### Side-effects

- **Operator experience unchanged.** «Restart auth-svc» button in the admin panel works identically. The proxy is transparent for allowed verbs.
- **One extra container.** ~5MB RAM, negligible CPU. Acceptable on the 3.7 GiB VPS.
- **Logs of denied verbs are a new signal.** Useful for incident detection: if `system-control` ever tries an unexpected verb, that is a real anomaly.

### Versioning

`network_mode: host` containers and a new compose service. Per the project conventions — this is a **MINOR** (new infra component, no breaking contract change). Plan: `v1.13.x → v1.14.0`.

## Alternatives considered

### Option B — Replace docker.sock with `systemctl restart Docker-compose@lotsman.service` via PolicyKit

`system-control` would shell out to `systemctl --user restart <service>` (or `pkexec ... systemctl restart`). PolicyKit rule restricts which units the `lotsman` (or dedicated `lotsman-control`) user can manage.

**Pros:**
- No new container, no new image to maintain.
- No socket mounting at all — privilege model uses standard Linux mechanisms (`polkit`).
- Aligned with what a systemd-only ops engineer would do anyway.

**Cons:**
- Requires sudoer-like setup on the host, which is **outside** the compose-deployed scope. production runbook becomes more host-coupled (currently all Лоцман-specific logic lives in compose).
- Restart granularity is unit-level, not container-level — has to map "restart auth-svc" → `systemctl restart Docker-container-lotsman_auth_svc.service`, requires Docker generate systemd or Docker-restart units.
- Harder to test in dev (dev uses Docker-compose, not systemd-managed containers).
- More surface for misconfiguration of the PolicyKit rule.

**Verdict:** rejected — the host coupling and dev/prod divergence cost outweighs the savings.

### Option C — Remove `system-control` entirely; rely on manual restart via SSH

`system-control` would disappear; the super-admin UI loses the «Restart» actions; operator does `ssh lotsman 'Docker restart <svc>'` by runbook.

**Pros:**
- Simplest possible threat model — no docker.sock anywhere.
- One fewer service to operate.

**Cons:**
- Loses ADR-0006 §5 capability (recovery actions from UI). Super-admin must SSH for routine ops they currently do from the browser.
- Two real users only — but they include a non-technical admin (`admin@example.com`). Forcing them to SSH is a UX regression.

**Verdict:** rejected — capability loss is real and unjustified given that Option A neutralises the threat at low cost.

### Option D — Hand-rolled FastAPI proxy instead of Tecnativa image

Same shape as Option A but a project-local container instead of a third-party image.

**Pros:**
- No supply-chain dependency on Tecnativa.
- Logging and metrics can match the rest of Лоцман stack from day one.

**Cons:**
- We become responsible for getting the allow-list right (Tecnativa has shipped this since 2018, multi-org-reviewed).
- More code to write, test, and maintain.

**Verdict:** Tecnativa is the safer default *for the initial implementation*. If supply-chain audit later flags it, replacement is straightforward — the contract (NR-1..NR-7) is image-agnostic.

## Implementation plan handoff (for `ops`)

1. Add `docker-socket-proxy` service to `infra/compose.prod.yml` with the env-var allow-list above. Use Tecnativa image pinned to a specific digest (not tag — the project conventions hygiene).
2. Change `system-control` mount to the proxy's socket path.
3. Add `depends_on: [docker-socket-proxy]` (Docker-compose honours startup ordering even on `network_mode: host`).
4. Write `tests/integration/test_socket_proxy_allowlist.py` exercising at least:
   - allowed: `GET /containers/json`, `GET /containers/lotsman_auth_svc/json`, `POST /containers/lotsman_auth_svc/restart`
   - denied: `exec`, `create`, `copy`, `pull`, `kill`, `stop`, `start`, `commit`, `volumes/create`, `networks/create`, `info`, `events`, `images/json`
5. Deploy to production via standard compose-redeploy. Smoke: trigger «Restart auth-svc» from the super-admin UI, observe `Docker ps` shows `lotsman_auth_svc` restart while `docker-socket-proxy` logs the single allowed POST.
6. Add Grafana panel «system-control denied verbs in last 24h» — should be 0 in steady state.

`backend` is **not** in scope for this ADR — no application code changes.

## Status

**Proposed.** Implementation triggered by operator approval. Estimated effort: **2 hours** (compose change + tests + smoke). No data migration. No downtime if rolled out with `Docker-compose up -d --no-recreate system-control` after the proxy is healthy.
