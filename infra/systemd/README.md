# Лоцман — host systemd integration (boot & crash persistence)

These units make the **podman-compose** deployment (`pod_lotsman`, host networking,
rootful podman) survive host reboots and in-session crashes. They were added after the
June 2026 PreProd outage, whose root cause was that the pod had **no boot-persistence**:
after a host reboot nothing restarted it, so `/api/*` returned 502 for days while nginx
kept serving the static SPA. See [`docs/deployment/preprod-runbook.md`](../../docs/deployment/preprod-runbook.md).

| File | Installs to | Role |
|---|---|---|
| `lotsman.service` | `/etc/systemd/system/` | Boot: `podman pod start pod_lotsman` via the wrapper; graceful stop. |
| `lotsman-observability.service` | `/etc/systemd/system/` | Boot: `podman start lotsman_prometheus lotsman_node_exporter` (siblings of the pod). |
| `lotsman-watchdog.service` + `.timer` | `/etc/systemd/system/` | Every 2 min: idempotent self-heal of any down pod member. |
| `../tmpfiles.d/lotsman-docker-sock.conf` | `/etc/tmpfiles.d/` | Recreate `/run/docker.sock` → `/run/podman/podman.sock` at boot (for `system-control`). |
| `../../scripts/lotsman-pod-up.sh` | `/opt/lotsman/scripts/` | Wrapper used by the boot unit and the watchdog. |
| `../cron.d/lotsman-backup` | `/etc/cron.d/` | Daily validated `pg_dumpall` via `scripts/backup-pg.sh`. |

> **Observability containers are created elsewhere.** `lotsman-observability.service` only
> *(re)starts already-created* containers; it never creates them. `lotsman_prometheus` comes from the
> [`compose.observability.yml`](../compose.observability.yml) overlay (`make obs-up`). `lotsman_node_exporter`
> is **not** defined in any compose file in this repo — it is provisioned on the host out-of-band — so
> create it before enabling this unit, or drop it from the unit's `ExecStart`/`ExecStop`.

## Why a wrapper + watchdog (not `podman-restart.service`)

- `podman-restart.service` only starts containers with `restart-policy=always`; ours are
  `unless-stopped`, so it would **not** cover them. We keep `unless-stopped` and drive the
  pod from `lotsman.service` instead (no dependency on that quirk).
- The oneshot's `Restart=on-failure` only fires on a failed *start*; it will **not** revive a
  pod that dies after a successful boot. The **watchdog timer** provides that in-session
  self-heal. `podman pod start` is idempotent, so the watchdog is a no-op on a healthy pod.
- Success is judged by **web-bff answering on `:8080/healthz`**, never the container
  healthchecks (which currently probe `:8000` while services listen on 8001–8080 — see
  [runbook follow-up #3](../../docs/deployment/preprod-runbook.md#follow-ups-deliberately-deferred--need-a-decision--external-resource)).

## Install

**Prerequisite:** the pod (`pod_lotsman`) and the observability containers must already exist —
these units start them, they do not create them. Bring them up first: the pod via your normal deploy,
and `lotsman_prometheus` via `make obs-up` (the [`compose.observability.yml`](../compose.observability.yml)
overlay). `lotsman_node_exporter` is host-provisioned and not in this repo (see the note above the table).

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
