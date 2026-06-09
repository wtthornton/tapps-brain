#!/usr/bin/env bash
# Fast local Docker iteration: reload the brain container, then live smoke.
#
# Usage:
#   bash scripts/dev-deploy.sh          # from repo root
#   make dev-deploy                     # via Makefile
#
# First-time stack: run `make hive-deploy` once, then use dev-deploy for inner loop.
#
# Force migrate sidecar: MIGRATE=1 make dev-deploy

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}"

make check-brain-env
make check-compose-isolation

if [[ "${MIGRATE:-0}" == "1" ]] || bash scripts/migrations-changed.sh; then
  echo "==> dev-deploy: hive-reload (migrations changed or MIGRATE=1)"
  make hive-reload
else
  echo "==> dev-deploy: hive-reload-http (code-only)"
  make hive-reload-http
fi

make brain-smoke-live
