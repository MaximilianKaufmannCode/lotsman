#!/usr/bin/env bash
# Лоцман — guarded restore of a pg_dumpall backup.
# SAFETY: no default target; restoring into the LIVE container requires an explicit
# extra flag; a fresh pre-restore dump is always taken first.
#
# Usage:
#   restore-pg.sh --file <backup.sql.gz> --target <container> [--i-really-mean-it]
# Examples:
#   restore-pg.sh --file /opt/lotsman/backups/latest-good.sql.gz --target lotsman_pg_scratch
#   restore-pg.sh --file /opt/lotsman/backups/daily-2026-06-22.sql.gz --target lotsman_postgres --i-really-mean-it
set -Eeuo pipefail

FILE=""; TARGET=""; CONFIRM=0
while [ $# -gt 0 ]; do
  case "$1" in
    --file) FILE="$2"; shift 2;;
    --target) TARGET="$2"; shift 2;;
    --i-really-mean-it) CONFIRM=1; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

[ -n "$FILE" ]   || { echo "ERROR: --file is required" >&2; exit 2; }
[ -n "$TARGET" ] || { echo "ERROR: --target is required (no default, on purpose)" >&2; exit 2; }
[ -f "$FILE" ]   || { echo "ERROR: file not found: $FILE" >&2; exit 2; }
gunzip -t "$FILE" || { echo "ERROR: $FILE is not a valid gzip" >&2; exit 2; }

if [ "$TARGET" = "lotsman_postgres" ] && [ "$CONFIRM" -ne 1 ]; then
  echo "REFUSING to restore into the LIVE container 'lotsman_postgres' without --i-really-mean-it." >&2
  echo "Restore into a scratch container first and verify, then re-run with the flag." >&2
  exit 3
fi

podman exec "$TARGET" pg_isready -U postgres -q || { echo "ERROR: target $TARGET not ready" >&2; exit 4; }

# Always take a fresh pre-restore safety dump of the TARGET first.
pre="/opt/lotsman/backups/pre-restore-$(date +%F-%H%M)-${TARGET}.sql.gz"
echo "Taking pre-restore safety dump of $TARGET -> $pre"
podman exec "$TARGET" pg_dumpall -U postgres --clean --if-exists 2>/dev/null | gzip -c > "$pre"
gunzip -t "$pre" && echo "pre-restore dump OK ($(stat -c%s "$pre")B)"

echo "Restoring $FILE into $TARGET ..."
gunzip -c "$FILE" | podman exec -i "$TARGET" psql -U postgres -v ON_ERROR_STOP=1 -d postgres
echo "Restore complete. Pre-restore safety dump kept at: $pre"
