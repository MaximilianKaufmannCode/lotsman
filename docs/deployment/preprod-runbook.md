# Лоцман PreProd — Operations Runbook

Host: `it143.fvds.ru` / `91.107.123.44` (alias `loodsman.skorpioshka.ru`). Rootful **podman 5.4.2**.
Deploy dir: `/opt/lotsman/Lotsman`. Orchestration: **podman-compose** with `infra/compose.preprod.yml` + `../.env`,
project `lotsman` → pod **`pod_lotsman`** (11 containers). Observability (`lotsman_prometheus`,
`lotsman_node_exporter`) is a **separate** project, NOT in the pod. Ingress is **host nginx** (systemd)
at `/etc/nginx/sites-enabled/lotsman`; it terminates TLS, serves the SPA, and proxies `/api` → `127.0.0.1:8080`.

All app services use `network_mode: host` and bind `127.0.0.1`: postgres 5432, redis 6379,
auth 8001, registry 8002, notification 8003, audit 8004, system-control 8005, **web-bff 8080**.

## June 2026 outage — root cause

Host rebooted 2026-06-18; the podman stack had **no boot-persistence** (no systemd unit;
`podman-restart.service` disabled — and it only covers `restart=always`, not our `unless-stopped`).
The pod stayed down ~4 days. nginx kept serving the static SPA, but every `/api/*` returned **502**,
so nobody could log in. Daily DB backups were also silently broken (20-byte empty gzips).

## What now keeps it up (boot + crash persistence)

| Unit | Role |
|---|---|
| `lotsman.service` (oneshot, enabled) | On boot runs `lotsman-pod-up.sh` → `podman pod start pod_lotsman`; graceful `ExecStop` (pg fast-stop then pod stop). Ordered `Before=nginx`. |
| `lotsman-observability.service` (enabled) | Starts `lotsman_prometheus` + `lotsman_node_exporter` (they are outside the pod). |
| `lotsman-watchdog.timer` (every 2 min) | Re-runs `lotsman-pod-up.sh`; idempotent — restarts any down pod member in-session. |
| `podman.socket` (enabled) | Provides `/run/podman/podman.sock`; `/etc/tmpfiles.d/lotsman-docker-sock.conf` symlinks `/run/docker.sock` → it (for system-control). |

`lotsman-pod-up.sh` success criterion = **web-bff answers on `:8080/healthz`** (never the container
healthchecks, which probe the wrong port). It tolerates `system_control` being down.

### Common ops
```bash
systemctl status lotsman.service lotsman-watchdog.timer
podman pod ps ; podman ps -a --filter pod=pod_lotsman
systemctl restart lotsman.service          # safe re-assert of the whole pod
journalctl -b 0 -u lotsman.service
```

### Reboot test (acceptance gate — re-run after any change that recreates containers)
```bash
# pre: validated backup + baseline exist; units enabled
systemctl reboot
# after, with NO manual action, all must hold:
podman pod ps                              # pod_lotsman Running (Degraded only if system_control down)
curl -sk -o/dev/null -w '%{http_code}\n' -X POST -d '{}' \
  -H 'content-type: application/json' https://loodsman.skorpioshka.ru/api/v1/auth/login   # 4xx, NOT 502
curl -s -o/dev/null -w '%{http_code}\n' 127.0.0.1:9090/prometheus/-/ready                 # 200
```

## Backups

`/opt/lotsman/scripts/backup-pg.sh` via `/etc/cron.d/lotsman-backup` (daily 03:00, retention 14d).
Validates every dump (gzip + min-size 50KB + cluster header + 4 schemas + 4 `_app` roles + data
lines + ≥50 objects), promotes atomically, updates `latest-good.sql.gz`, writes a node_exporter
textfile metric (`lotsman_pg_backup_success`), and **fails loud** (non-zero + syslog `user.err` +
optional external dead-man) — a silent empty backup is now impossible.

**Restore (guarded):**
```bash
/opt/lotsman/scripts/restore-pg.sh --file /opt/lotsman/backups/latest-good.sql.gz --target <scratch>
# live target requires the explicit --i-really-mean-it flag and takes a pre-restore dump first
```

## Follow-ups (deliberately deferred — need a decision / external resource)

1. **`system_control` (admin plane, :8005)** is down: its overlay has a stale `/var/run/docker.sock`
   *directory* from past failed mounts → recreate it once (`podman-compose -p lotsman -f
   infra/compose.preprod.yml up -d --force-recreate system_control`) after confirming it only touches
   that service. Restoring it exposes the **rootful podman socket** to a container (root-equivalent) —
   mitigated by loopback-only + never-public (8005 not in nginx). Optional hardening: ADR-0009
   socket-proxy (note: its EXEC:0 allow-list breaks the live `/v1/migrate` + `/v1/logs`).
2. **External dead-man URL** for backup + a blackbox POST check on the public login path
   (`LOTSMAN_BACKUP_HC_URL` in cron; e.g. healthchecks.io) — the only detector that survives a full
   outage (in-pod prometheus is down exactly when the stack is). Wire to a human channel.
3. **Healthcheck / prometheus-scrape port fix** (cosmetic): compose healthchecks + prometheus targets
   hit `:8000`, but services listen on 8001–8080 → containers show `unhealthy` though they serve fine.
   Fix per-service (canary, never bare `down`/`up -d`), then re-run the reboot test.
4. **Alertmanager / webhook** so in-pod alert rules actually route.

## Repo divergence (server is the source of truth for these)

- `infra/compose.preprod.yml` exists **only on the server**; repo `compose.prod.yml` does NOT match live.
- `/etc/nginx/sites-enabled/lotsman` is host-managed (differs from repo `infra/nginx/`).
- `.env` and secrets are server-only by design.
