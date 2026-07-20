#!/usr/bin/env bash
# Detect whether migrate-related files changed since the last hive-reload stamp.
#
# Usage:
#   scripts/migrations-changed.sh          → exit 0 if migrate should run, 1 if not
#   scripts/migrations-changed.sh --stamp  → write current fingerprint to stamp file
#
# Used by `make dev-deploy` to skip the migrate sidecar when only Python code changed.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="${ROOT}/.docker-last-migrate-sha"

MIGRATE_PATHS=(
  src/tapps_brain/migrations/
  docker/migrate-entrypoint.sh
  docker/Dockerfile.migrate
)

migration_fingerprint() {
  cd "$ROOT"
  if command -v git >/dev/null 2>&1 && git rev-parse --git-dir >/dev/null 2>&1; then
    # --others --exclude-standard includes NEW (untracked) migration files;
    # plain ls-files only saw tracked ones, so dev-deploy skipped the migrate
    # sidecar for a freshly added migration until it was committed.
    { git ls-files "${MIGRATE_PATHS[@]}"; \
      git ls-files --others --exclude-standard "${MIGRATE_PATHS[@]}"; } \
      | LC_ALL=C sort -u \
      | xargs -r sha256sum \
      | sha256sum \
      | awk '{print $1}'
    return
  fi
  find "${MIGRATE_PATHS[@]}" -type f 2>/dev/null \
    | LC_ALL=C sort \
    | xargs -r sha256sum \
    | sha256sum \
    | awk '{print $1}'
}

write_stamp() {
  migration_fingerprint >"$STAMP"
}

if [[ "${1:-}" == "--stamp" ]]; then
  write_stamp
  exit 0
fi

current="$(migration_fingerprint)"
stored=""
if [[ -f "$STAMP" ]]; then
  stored="$(cat "$STAMP")"
fi

if [[ "$current" != "$stored" ]]; then
  exit 0
fi
exit 1
