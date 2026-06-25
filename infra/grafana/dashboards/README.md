# Grafana Dashboards

This directory holds JSON dashboard definitions for Лоцман.

> [!IMPORTANT]
> **Datasources** (Prometheus + Loki) are provisioned automatically from
> [`../provisioning/datasources/datasources.yml`](../provisioning/datasources/datasources.yml).
> **Dashboards are not yet auto-provisioned.** This directory is mounted into Grafana
> read-only at `/var/lib/grafana/dashboards`, but there is no dashboards *provider*
> config under `../provisioning/dashboards/`. Until one exists, Grafana will not
> import these JSON files on startup — see [Importing dashboards](#importing-dashboards).

## Running the stack

Grafana ships in the observability overlay, not the base dev stack. Bring it up with:

```sh
make obs-up    # Prometheus, Grafana, Loki, Promtail (see infra/compose.observability.yml)
make obs-down  # tear it down
```

`obs-up` runs `docker compose -f infra/compose.dev.yml -f infra/compose.observability.yml up -d`.
Ports (loopback only): **Prometheus 9090**, **Grafana 3000**. Loki (3100) is internal —
Promtail pushes to it.

## Metrics sources

All six backend services expose `/metrics` on port `8000`, but Prometheus scrapes
only five of them (jobs `auth-svc`, `registry-svc`, `notification-svc`, `audit-svc`,
`web-bff` in [`../../prometheus/prometheus.yml`](../../prometheus/prometheus.yml)). The
sixth, `system-control`, is a dev-only privileged sidecar that also exposes `/metrics`
but is not listed in `prometheus.yml` (absent in prod). So dashboards built against the
scraped metrics have live data to read.

## Planned dashboards

None of these exist yet — they are placeholders for the panels `ops` intends to build:

| File | Status | Description |
|------|--------|-------------|
| `lotsman-overview.json` | planned | Per-service RPS, p50/p95/p99 latency, error rate |
| `lotsman-database.json` | planned | Postgres connections, slow queries, table sizes |
| `lotsman-notifications.json` | planned | Queue depth, send success/failure rate, retry count |
| `lotsman-audit.json` | planned | Event ingestion rate, partition sizes |

## Importing dashboards

Dropping a JSON file here is **not** enough on its own — a dashboards provider config
must also exist. Two steps:

1. **Add a provider config** at `../provisioning/dashboards/<name>.yml` pointing Grafana
   at `/var/lib/grafana/dashboards`. This file does not exist yet; create it once and it
   covers every JSON in this directory.
2. **Place the `.json` file** in this directory.

Then reload Grafana so it picks up the new files:

```sh
# dev / local (Compose overlay)
make obs-down && make obs-up
# or restart just Grafana:
docker compose -f infra/compose.dev.yml -f infra/compose.observability.yml restart grafana
```

Grafana then provisions the dashboard under the **Лоцман** folder.

## Naming convention

- Dashboard title: descriptive English name
- Dashboard uid: `lotsman-<scope>` (e.g., `lotsman-overview`)
- Folder: `Лоцман`

## Datasource dependencies

All dashboards reference the `prometheus` and `loki` datasource UIDs defined in
[`../provisioning/datasources/datasources.yml`](../provisioning/datasources/datasources.yml).
Do not hardcode datasource names in dashboard JSON — always reference by UID.

---

_Last updated: 2026-06-25_
