# Grafana Dashboards

This directory holds JSON dashboard definitions for Лоцман. They are provisioned
automatically by Grafana on startup via the provisioning configuration.

## Planned dashboards

The following dashboards will be added by `ops` as the backend
services deliver their `/metrics` endpoints:

| File | Description |
|------|-------------|
| `lotsman-overview.json` | Per-service RPS, p50/p95/p99 latency, error rate |
| `lotsman-database.json` | Postgres connections, slow queries, table sizes |
| `lotsman-notifications.json` | Queue depth, send success/failure rate, retry count |
| `lotsman-audit.json` | Event ingestion rate, partition sizes |

## Importing dashboards

1. Place the `.json` file in this directory.
2. Restart the Grafana container: `docker restart lotsman_grafana`
3. Grafana will auto-provision the dashboard under the "Лоцман" folder.

## Naming convention

- Dashboard title: descriptive English name
- Dashboard uid: `lotsman-<scope>` (e.g., `lotsman-overview`)
- Folder: `Лоцман`

## Datasource dependencies

All dashboards use the `prometheus` and `loki` datasource UIDs defined in
`infra/grafana/provisioning/datasources/datasources.yml`. Do not hardcode
datasource names in dashboard JSON — always reference by UID.
