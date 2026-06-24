#!/usr/bin/env bash
# Лоцман PreProd — bring the podman pod up and verify the user-facing path.
# Used by lotsman.service (boot) and lotsman-watchdog.service (every 2 min).
# Idempotent: `podman pod start` on a running pod is a no-op and also (re)starts
# any individual member that is currently down. Success == web-bff answers on :8080,
# so a tolerated-down system_control never blocks recovery and we never trust the
# (known-wrong-port) container healthchecks.
set -uo pipefail
POD=pod_lotsman
PROBE_URL="http://127.0.0.1:8080/healthz"   # web-bff — listen port verified on box
MAX_WAIT=60                                  # capped so nginx is not held long on a bad boot

if ! podman pod exists "$POD"; then
  echo "FATAL: pod $POD does not exist" >&2
  exit 1
fi

podman pod start "$POD"; rc=$?
echo "podman pod start $POD -> rc=$rc"
# Informational only: list pod members that are not running (system_control may be
# intentionally down until its socket recreate is done — see README-ops.md).
podman ps -a --filter "pod=$POD" --format '{{.Names}} {{.State}}' \
  | awk '$2 != "running" { print "  not-running: " $0 }'

deadline=$(( $(date +%s) + MAX_WAIT ))
while (( $(date +%s) < deadline )); do
  if curl -fsS --max-time 3 "$PROBE_URL" >/dev/null 2>&1; then
    echo "OK: web-bff healthy at $PROBE_URL"
    exit 0
  fi
  sleep 3
done
echo "ERROR: web-bff not healthy at $PROBE_URL within ${MAX_WAIT}s" >&2
exit 1
