#!/usr/bin/env bash
# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

# scripts/check-version-bump.sh
#
# Pre-commit guard for the versioning policy in CONTRIBUTING.md.
#
# If staged changes touch code/migrations/infra (services/, web/src/, infra/,
# shared/) — refuse the commit unless web/package.json is ALSO staged with a
# bumped `version` field.
#
# Usage:
#   bash scripts/check-version-bump.sh         # check staged
#   bash scripts/check-version-bump.sh --hook  # install as git pre-commit hook
#
# To skip (rare cases — only when explicitly justified, e.g. WIP feature
# branch with intentional pre-release): VERSION_BUMP_SKIP=1 git commit ...

set -euo pipefail

if [ "${1:-}" = "--hook" ]; then
    HOOK_PATH=".git/hooks/pre-commit"
    cat > "$HOOK_PATH" <<'HOOK'
#!/usr/bin/env bash
exec bash scripts/check-version-bump.sh
HOOK
    chmod +x "$HOOK_PATH"
    echo "✅ Installed pre-commit hook → $HOOK_PATH"
    exit 0
fi

if [ "${VERSION_BUMP_SKIP:-}" = "1" ]; then
    echo "⚠ VERSION_BUMP_SKIP=1 — bypassing version-bump check (CONTRIBUTING.md)"
    exit 0
fi

# Files that count as "code/architecture changes" requiring a bump.
CODE_PATTERN='^(services/|web/src/|infra/|shared/|services/system-control/|services/auth-service/alembic/|services/registry-service/alembic/|services/notification-service/alembic/|services/audit-service/alembic/)'

# Files that are docs-only (no bump required).
# (We don't whitelist; we just check that nothing under CODE_PATTERN is staged
# alone without a package.json bump.)

staged=$(git diff --cached --name-only --diff-filter=ACMR 2>/dev/null || true)

if [ -z "$staged" ]; then
    # No staged files — `git commit` will fail anyway; not our problem.
    exit 0
fi

# Are any staged files in code paths?
if ! echo "$staged" | grep -qE "$CODE_PATTERN"; then
    # Pure docs / config / scripts commit — bump not required.
    exit 0
fi

# Code is being changed. Is web/package.json staged with a version change?
if ! echo "$staged" | grep -qx 'web/package.json'; then
    cat <<EOF >&2

═══════════════════════════════════════════════════════════════════
🛑  VERSION BUMP REQUIRED  —  CONTRIBUTING.md

This commit touches code/migrations/infra:
$(echo "$staged" | grep -E "$CODE_PATTERN" | head -10 | sed 's/^/  · /')
$([ "$(echo "$staged" | grep -cE "$CODE_PATTERN")" -gt 10 ] && echo "  · ... and more")

But web/package.json is NOT staged.

Per the versioning policy:
  · MAJOR  — breaking architecture changes
  · MINOR  — new feature / endpoint / page (no breakage)
  · PATCH  — bug fix / security patch / UX glitch

Steps:
  1. Edit web/package.json — bump "version": "X.Y.Z" → next
  2. Add a section to CHANGELOG.md (## [X.Y.Z] — YYYY-MM-DD)
  3. git add web/package.json CHANGELOG.md
  4. Re-run git commit

To bypass (only when intentional — e.g. mid-feature WIP):
  VERSION_BUMP_SKIP=1 git commit ...

═══════════════════════════════════════════════════════════════════

EOF
    exit 1
fi

# package.json is staged. Check that the version field actually changed.
if ! git diff --cached web/package.json 2>/dev/null | grep -qE '^\+\s*"version":'; then
    cat <<EOF >&2

═══════════════════════════════════════════════════════════════════
🛑  VERSION BUMP REQUIRED  —  CONTRIBUTING.md

web/package.json IS staged but the "version" line is unchanged.

Edit web/package.json — bump "version" — re-stage — re-commit.

═══════════════════════════════════════════════════════════════════

EOF
    exit 1
fi

# Bonus: warn if CHANGELOG.md is not also being updated.
if ! echo "$staged" | grep -qx 'CHANGELOG.md'; then
    echo ""
    echo "⚠ Warning: CHANGELOG.md is not staged. Per §8 each bump should add a new section."
    echo "  Continuing — but please update CHANGELOG.md in the next commit if you skipped it intentionally."
    echo ""
fi

# All clear.
exit 0
