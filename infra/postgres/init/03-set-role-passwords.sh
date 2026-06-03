#!/usr/bin/env bash
# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

# 02-set-role-passwords.sh
# Sets passwords on the per-service Postgres roles created by 01-schemas-and-roles.sql.
# Runs as the postgres superuser during container first-start via /docker-entrypoint-initdb.d/.
#
# Passwords are read from environment variables injected by compose / Docker secrets.
# If a variable is unset or empty, the role is left without a password (dev default;
# acceptable for local dev where Postgres is not exposed beyond 127.0.0.1).
#
# Variables consumed:
#   AUTH_PG_PASSWORD         — password for auth_app role
#   REGISTRY_PG_PASSWORD     — password for registry_app role
#   NOTIFICATION_PG_PASSWORD — password for notification_app role
#   AUDIT_PG_PASSWORD        — password for audit_app role

set -euo pipefail

psql_cmd() {
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" "$@"
}

echo "==> 02-set-role-passwords: configuring application role passwords"

if [ -n "${AUTH_PG_PASSWORD:-}" ]; then
    psql_cmd -c "ALTER ROLE auth_app PASSWORD '${AUTH_PG_PASSWORD}';"
    echo "  auth_app password set"
else
    echo "  AUTH_PG_PASSWORD not set — auth_app has no password (dev mode)"
fi

if [ -n "${REGISTRY_PG_PASSWORD:-}" ]; then
    psql_cmd -c "ALTER ROLE registry_app PASSWORD '${REGISTRY_PG_PASSWORD}';"
    echo "  registry_app password set"
else
    echo "  REGISTRY_PG_PASSWORD not set — registry_app has no password (dev mode)"
fi

if [ -n "${NOTIFICATION_PG_PASSWORD:-}" ]; then
    psql_cmd -c "ALTER ROLE notification_app PASSWORD '${NOTIFICATION_PG_PASSWORD}';"
    echo "  notification_app password set"
else
    echo "  NOTIFICATION_PG_PASSWORD not set — notification_app has no password (dev mode)"
fi

if [ -n "${AUDIT_PG_PASSWORD:-}" ]; then
    psql_cmd -c "ALTER ROLE audit_app PASSWORD '${AUDIT_PG_PASSWORD}';"
    echo "  audit_app password set"
else
    echo "  AUDIT_PG_PASSWORD not set — audit_app has no password (dev mode)"
fi

echo "==> 02-set-role-passwords: done"
