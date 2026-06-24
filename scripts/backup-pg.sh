#!/usr/bin/env bash
# Лоцман PreProd — hardened PostgreSQL logical backup (pg_dumpall) with validation.
# Replaces the previous script, which silently produced 20-byte empty gzips while the
# DB was down (gunzip -t passes on an empty gzip; no size/content gate; no alert).
#
# Guarantees:
#   - never KEEPS a backup that is empty / too small / corrupt / schema-only;
#   - fails LOUD (non-zero exit + syslog user.err + optional external dead-man + node_exporter metric);
#   - decompresses ONCE to a temp file and greps the FILE (no `gunzip | grep` -> no SIGPIPE/141 under pipefail);
#   - count gates use `grep -c ... || true` (grep exits 1 at count 0 -> would otherwise trip ERR trap);
#   - atomic promote (.partial -> final); retention runs only after success and never deletes the newest.
set -Eeuo pipefail

CONTAINER="lotsman_postgres"
PGUSER="postgres"
BACKUP_DIR="/opt/lotsman/backups"
RETENTION_DAYS=14
MIN_BYTES=50000                 # known-good dumps are ~280KB; 20-byte empties are killed here
OBJ_FLOOR=50                    # live dump has ~116 CREATE TABLE/SEQUENCE/INDEX/TYPE/FUNCTION
EXPECTED_SCHEMAS=(auth registry notification audit)
EXPECTED_ROLES=(auth_app registry_app notification_app audit_app)
TEXTFILE_DIR="/var/lib/node_exporter/textfile"   # metric is best-effort (textfile collector may be off)
HEALTHCHECK_URL="${LOTSMAN_BACKUP_HC_URL:-}"     # external dead-man; set in cron env to enable paging
ALERT_EMAIL="${LOTSMAN_BACKUP_EMAIL:-}"          # only used if an MTA exists
LOG_TAG="lotsman-backup"

ts="$(date +%Y-%m-%d)"
final="${BACKUP_DIR}/daily-${ts}.sql.gz"
tmp="${BACKUP_DIR}/.daily-${ts}.sql.gz.partial"
plain="$(mktemp)"
errf="$(mktemp)"
mkdir -p "$BACKUP_DIR" "$TEXTFILE_DIR" 2>/dev/null || true

log()    { logger -t "$LOG_TAG" -- "$*" 2>/dev/null || true; echo "[$(date -Is)] $*"; }
metric() { printf 'lotsman_pg_backup_success %s\nlotsman_pg_backup_size_bytes %s\nlotsman_pg_backup_timestamp_seconds %s\n' \
             "$1" "$2" "$(date +%s)" > "${TEXTFILE_DIR}/lotsman_backup.prom.$$" 2>/dev/null \
             && mv -f "${TEXTFILE_DIR}/lotsman_backup.prom.$$" "${TEXTFILE_DIR}/lotsman_backup.prom" 2>/dev/null || true; }
alert()  { logger -t "$LOG_TAG" -p user.err -- "$1" 2>/dev/null || true;
           if [ -n "$ALERT_EMAIL" ] && command -v mail >/dev/null 2>&1; then
             printf '%s\nHost: %s\nTime: %s\n' "$1" "$(hostname)" "$(date -Is)" | mail -s "[ALERT] Лоцман PreProd backup" "$ALERT_EMAIL" || true; fi
           [ -n "$HEALTHCHECK_URL" ] && curl -fsS -m10 --retry 3 "${HEALTHCHECK_URL}/fail" -o /dev/null 2>/dev/null || true; }
cleanup(){ rm -f "$tmp" "$plain" "$errf" 2>/dev/null || true; }
fail()   { log "FAILED: $1"; metric 0 0; alert "Лоцман PG BACKUP FAILED: $1"; cleanup; exit 1; }
trap 'fail "unexpected error at line $LINENO"' ERR
trap cleanup EXIT

# Gate 1 — DB must be accepting connections (the exact outage condition that made empties)
podman exec "$CONTAINER" pg_isready -U "$PGUSER" -q || fail "pg_isready: postgres not accepting connections"

# Dump -> gzip to temp. pipefail + explicit PIPESTATUS check surface a failed dump.
podman exec "$CONTAINER" pg_dumpall -U "$PGUSER" --clean --if-exists 2>"$errf" | gzip -c > "$tmp"
ps=("${PIPESTATUS[@]}")
[ "${ps[0]}" -eq 0 ] || fail "pg_dumpall rc=${ps[0]}: $(tr '\n' ' ' <"$errf")"
[ -s "$errf" ] && log "pg_dumpall stderr: $(tr '\n' ' ' <"$errf")"

# Gate 2 — gzip framing intact
gunzip -t "$tmp" || fail "gunzip -t failed (corrupt gzip)"
# Gate 3 — minimum size (kills the 20-byte empty gzip)
size="$(stat -c%s "$tmp")"
[ "$size" -ge "$MIN_BYTES" ] || fail "too small: ${size}B < ${MIN_BYTES}B"

# Decompress ONCE to a file; every content gate greps the FILE (no pipe -> no SIGPIPE).
gunzip -c "$tmp" > "$plain"
grep -q 'PostgreSQL database cluster dump' "$plain" || fail "missing cluster-dump header"
for s in "${EXPECTED_SCHEMAS[@]}"; do
  grep -Eq "CREATE SCHEMA( IF NOT EXISTS)? \"?${s}\"?" "$plain" || fail "schema '${s}' missing"
done
for r in "${EXPECTED_ROLES[@]}"; do
  grep -Eq "CREATE ROLE \"?${r}\"?" "$plain" || fail "role '${r}' missing"
done
grep -Eq '^(COPY |INSERT INTO )' "$plain" || fail "no COPY/INSERT data lines (schema-only/empty)"
obj="$(grep -cE '^(CREATE TABLE|CREATE SEQUENCE|CREATE INDEX|CREATE TYPE|CREATE FUNCTION)' "$plain" || true)"
[ "${obj:-0}" -ge "$OBJ_FLOOR" ] || fail "object count ${obj:-0} below floor ${OBJ_FLOOR}"

# Promote atomically — only a fully validated dump enters the pool.
mv -f "$tmp" "$final"
sha256sum "$final" > "${final}.sha256" 2>/dev/null || true
ln -sf "$(basename "$final")" "${BACKUP_DIR}/latest-good.sql.gz"
metric 1 "$size"
log "OK: ${final} (${size}B, ${obj} objects)"
[ -n "$HEALTHCHECK_URL" ] && curl -fsS -m10 --retry 3 "$HEALTHCHECK_URL" -o /dev/null 2>/dev/null || true

# Retention AFTER success; never delete the newest daily. No pipe in a capture.
newest=""
for f in $(ls -1t "${BACKUP_DIR}"/daily-*.sql.gz 2>/dev/null); do newest="$f"; break; done
while IFS= read -r d; do
  [ "$d" = "$newest" ] && continue
  log "rotated out: $d"
  rm -f "$d" "${d}.sha256"
done < <(find "${BACKUP_DIR}" -maxdepth 1 -name 'daily-*.sql.gz' -type f -mtime +"${RETENTION_DAYS}")

exit 0
