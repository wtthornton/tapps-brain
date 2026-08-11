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
#
# Readiness wait before the smoke stage is bounded at BRAIN_HEALTH_TIMEOUT
# seconds (default 180). Raise it on a slow host:
#   BRAIN_HEALTH_TIMEOUT=300 make dev-deploy

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}"

# Seconds to wait for tapps-brain-http to report healthy before smoking it.
# The compose healthcheck is interval 30s / start_period 20s / retries 3, so a
# cold start legitimately needs ~30s and an unhealthy container needs ~110s to
# exhaust its retries. 180s clears both without masking a container that is
# genuinely failing to come up.
BRAIN_HEALTH_TIMEOUT="${BRAIN_HEALTH_TIMEOUT:-180}"
BRAIN_CONTAINER="${BRAIN_CONTAINER:-tapps-brain-http}"

# Block until the container's own Docker health check reports healthy.
#
# TAP-5636: dev-deploy used to recreate the container and smoke it immediately.
# On a cold start the probe connected while uvicorn was still binding and died
# with `ConnectionResetError: [Errno 104] Connection reset by peer`, failing a
# deploy that had in fact succeeded. A deploy path that cries wolf on success
# trains the operator to re-run on failure, which is exactly the habit that
# lets a real crash-loop through.
#
# Docker's health status is the authority here rather than a second /healthz
# poll of our own: the compose `depends_on: service_healthy` chains already
# gate on it, and defining readiness twice invites the two to disagree.
# Echo one of: the container's health status, `none` when the container
# defines no healthcheck, or `missing` when it does not exist.
#
# The `{{if .State.Health}}` guard is deliberate. Rendering
# `{{.State.Health.Status}}` against a container without a healthcheck yields
# an empty string on some Docker versions and `<no value>` on others, so
# matching on either is a portability trap. Keeping this in its own function
# also stops a failing `docker inspect` from concatenating its partial stdout
# with a fallback value.
brain_health_status() {
  local out
  if out="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
              "$BRAIN_CONTAINER" 2>/dev/null)"; then
    printf '%s' "$out"
  else
    printf 'missing'
  fi
}

wait_for_brain_healthy() {
  local deadline=$((SECONDS + BRAIN_HEALTH_TIMEOUT))
  local status

  # Nothing to wait on without a healthcheck — continue rather than spinning
  # until the timeout on a container that can never report healthy.
  if [[ "$(brain_health_status)" == "none" ]]; then
    echo "==> dev-deploy: $BRAIN_CONTAINER defines no healthcheck; skipping the readiness wait"
    return 0
  fi

  echo "==> dev-deploy: waiting up to ${BRAIN_HEALTH_TIMEOUT}s for $BRAIN_CONTAINER to report healthy"
  while true; do
    status="$(brain_health_status)"
    case "$status" in
      healthy)
        echo "==> dev-deploy: $BRAIN_CONTAINER is healthy after ${SECONDS}s"
        return 0
        ;;
      missing)
        echo "ERROR: container $BRAIN_CONTAINER does not exist. Run 'make hive-deploy' first." >&2
        return 1
        ;;
    esac

    if (( SECONDS >= deadline )); then
      echo "ERROR: $BRAIN_CONTAINER did not report healthy within ${BRAIN_HEALTH_TIMEOUT}s" >&2
      echo "       last health status: ${status}" >&2
      echo "       restart count: $(docker inspect -f '{{.RestartCount}}' "$BRAIN_CONTAINER" 2>/dev/null || echo '?')" >&2
      echo "       --- last 40 log lines ---" >&2
      docker logs --tail 40 "$BRAIN_CONTAINER" >&2 2>&1 || true
      echo "       Raise the budget with BRAIN_HEALTH_TIMEOUT=<seconds> if this is a slow host." >&2
      return 1
    fi
    sleep 3
  done
}

make check-brain-env
make check-compose-isolation

if [[ "${MIGRATE:-0}" == "1" ]] || bash scripts/migrations-changed.sh; then
  echo "==> dev-deploy: hive-reload (migrations changed or MIGRATE=1)"
  make hive-reload
else
  echo "==> dev-deploy: hive-reload-http (code-only)"
  make hive-reload-http
fi

wait_for_brain_healthy

make brain-smoke-live
