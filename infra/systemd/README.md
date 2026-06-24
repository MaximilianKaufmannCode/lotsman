# Лоцман — host systemd integration (boot & crash persistence)

These units make the **podman-compose** deployment (`pod_lotsman`, host networking,
rootful podman) survive host reboots and in-session crashes. They were added after the
June 2026 PreProd outage, whose root cause was that the pod had **no boot-persistence**:
after a host reboot nothing restarted it, so `/api/*` returned 502 for days while nginx
kept serving the static SPA. See [`docs/deployment/preprod-runbook.md`](../../docs/deployment/preprod-runbook.md).

| File | Installs to | Role |
|---|---|---|
| `lotsman.service` | `/etc/systemd/system/` | Boot: `podman pod start pod_lotsman` via the wrapper; graceful stop. |
| `lotsman-observability.service` | `/etc/systemd/system/` | Boot: start `lotsman_prometheus` + `lotsman_node_exporter` (outside the pod). |
| `lotsman-watchdog.service` + `.timer` | `/etc/systemd/system/` | Every 2 min: idempotent self-heal of any down pod member. |
| `../tmpfiles.d/lotsman-docker-sock.conf` | `/etc/tmpfiles.d/` | Recreate `/run/docker.sock` → `/run/podman/podman.sock` at boot (for `system-control`). |
| `../../scripts/lotsman-pod-up.sh` | `/opt/lotsman/scripts/` | Wrapper used by the boot unit and the watchdog. |
| `../cron.d/lotsman-backup` | `/etc/cron.d/` | Daily validated `pg_dumpall` via `scripts/backup-pg.sh`. |

## Why a wrapper + watchdog (not `podman-restart.service`)

- `podman-restart.service` only starts containers with `restart-policy=always`; ours are
  `unless-stopped`, so it would **not** cover them. We keep `unless-stopped` and drive the
  pod from `lotsman.service` instead (no dependency on that quirk).
- The oneshot's `Restart=on-failure` only fires on a failed *start*; it will **not** revive a
  pod that dies after a successful boot. The **watchdog timer** provides that in-session
  self-heal. `podman pod start` is idempotent, so the watchdog is a no-op on a healthy pod.
- Success is judged by **web-bff answering on `:8080/healthz`**, never the container
  healthchecks (which currently probe the wrong port — see runbook follow-ups).

## Install

```bash
install -m0755 scripts/lotsman-pod-up.sh scripts/backup-pg.sh scripts/restore-pg.sh /opt/lotsman/scripts/
install -m0644 infra/systemd/lotsman*.service infra/systemd/lotsman-watchdog.timer /etc/systemd/system/
install -m0644 infra/tmpfiles.d/lotsman-docker-sock.conf /etc/tmpfiles.d/
install -m0644 infra/cron.d/lotsman-backup /etc/cron.d/lotsman-backup
systemctl enable --now podman.socket
systemd-tmpfiles --create /etc/tmpfiles.d/lotsman-docker-sock.conf
systemctl daemon-reload
systemctl enable --now lotsman.service lotsman-observability.service lotsman-watchdog.timer
```

Acceptance gate: a **real reboot** must bring everything back with no manual action, and
`POST https://<host>/api/v1/auth/login` must return a 4xx (not 502). Full procedure in the runbook.
