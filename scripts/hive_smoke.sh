#!/usr/bin/env bash
# scripts/hive_smoke.sh — End-to-end smoke test for the unified tapps-brain stack.
# (EPIC-067 STORY-067.5)
#
# Boots the full compose stack using temporary .env credentials, waits for
# health probes, asserts all endpoints return correct responses, then tears
# the stack down (including volumes).
#
# Usage:
#   bash scripts/hive_smoke.sh     # from repo root
#   make hive-smoke                # via Makefile target
#
# The script writes docker/.env with throwaway values for the duration of the
# run. If a real docker/.env exists, it is backed up and restored on exit.

set -euo pipefail

COMPOSE_FILE="docker/docker-compose.hive.yaml"
# Use a throwaway project name so this smoke run cannot collide with a live
# `tapps-brain` deployment on the same host.
COMPOSE="docker compose -p tapps-brain-smoke -f ${COMPOSE_FILE}"

# Use non-default ports to avoid clashing with a running live stack.
export TAPPS_VISUAL_PORT="${TAPPS_VISUAL_PORT:-18088}"
ADAPTER_PORT="${TAPPS_HTTP_PORT:-18080}"
export TAPPS_HTTP_PORT="${ADAPTER_PORT}"
# Keep operator MCP off the host loopback port used in dev (8090) — pick 18090.
export TAPPS_OPERATOR_MCP_PORT="${TAPPS_OPERATOR_MCP_PORT:-18090}"

# Non-default credentials for this smoke run (written to docker/.env below).
# Two distinct DB passwords: SMOKE_PASSWORD is the superuser the migrate
# sidecar connects as (`tapps`); SMOKE_RUNTIME_PASSWORD is what migrate then
# sets on the DML-only `tapps_runtime` role and what the brain container
# uses at runtime.  Keep both — TAP-1100.
SMOKE_PASSWORD="smoke-$(openssl rand -hex 12 2>/dev/null || echo testonly-password)"
SMOKE_RUNTIME_PASSWORD="smoke-$(openssl rand -hex 12 2>/dev/null || echo testonly-runtime-password)"
SMOKE_AUTH_TOKEN="smoke-$(openssl rand -hex 16 2>/dev/null || echo testonly-auth)"
SMOKE_ADMIN_TOKEN="smoke-$(openssl rand -hex 16 2>/dev/null || echo testonly-admin)"

MAX_WAIT=90   # seconds to wait for each health probe
PASS=0
FAIL=0

echo "==> tapps-brain unified-stack smoke test"
echo "    Visual port  : ${TAPPS_VISUAL_PORT}"
echo "    Adapter port : ${ADAPTER_PORT}"
echo "    Operator MCP : ${TAPPS_OPERATOR_MCP_PORT}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

pass() { echo "  [PASS] $*"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $*"; FAIL=$((FAIL + 1)); }

wait_for_url() {
    local url="$1"
    local label="$2"
    local elapsed=0
    printf "    Waiting for %s " "$label"
    until curl -sf -o /dev/null "$url" 2>/dev/null; do
        sleep 3
        elapsed=$((elapsed + 3))
        printf "."
        if [[ $elapsed -ge $MAX_WAIT ]]; then
            echo " TIMEOUT"
            return 1
        fi
    done
    echo " ready (${elapsed}s)"
}

assert_http() {
    local url="$1"
    local expected_status="$2"
    local label="$3"
    local actual
    actual=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
    if [[ "$actual" == "$expected_status" ]]; then
        pass "$label (HTTP $actual)"
    else
        fail "$label — expected HTTP $expected_status, got $actual"
    fi
}

assert_json_field_nonempty() {
    local url="$1"
    local field="$2"
    local label="$3"
    local body
    body=$(curl -sf "$url" 2>/dev/null || echo "{}")
    local actual
    actual=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('$field',''))" 2>/dev/null || echo "")
    if [[ -n "$actual" ]]; then
        pass "$label (.${field}='${actual}')"
    else
        fail "$label — field '${field}' was empty or missing"
    fi
}

# ---------------------------------------------------------------------------
# docker/.env management — back up the real file, write smoke values, restore
# on exit.
# ---------------------------------------------------------------------------

ENV_FILE="docker/.env"
ENV_BACKUP=""
if [[ -f "$ENV_FILE" ]]; then
    ENV_BACKUP="${ENV_FILE}.smoke-backup.$$"
    cp "$ENV_FILE" "$ENV_BACKUP"
fi

restore_env() {
    if [[ -n "$ENV_BACKUP" && -f "$ENV_BACKUP" ]]; then
        mv "$ENV_BACKUP" "$ENV_FILE"
    else
        rm -f "$ENV_FILE"
    fi
}

write_smoke_env() {
    cat > "$ENV_FILE" <<EOF
# Smoke-test throwaway — do NOT commit. Restored on exit.
TAPPS_BRAIN_DB_PASSWORD=${SMOKE_PASSWORD}
TAPPS_BRAIN_RUNTIME_PASSWORD=${SMOKE_RUNTIME_PASSWORD}
TAPPS_BRAIN_AUTH_TOKEN=${SMOKE_AUTH_TOKEN}
TAPPS_BRAIN_ADMIN_TOKEN=${SMOKE_ADMIN_TOKEN}
TAPPS_HTTP_PORT=${ADAPTER_PORT}
TAPPS_VISUAL_PORT=${TAPPS_VISUAL_PORT}
TAPPS_OPERATOR_MCP_PORT=${TAPPS_OPERATOR_MCP_PORT}
EOF
}

# ---------------------------------------------------------------------------
# Cleanup: tear down stack, restore .env, print results
# ---------------------------------------------------------------------------

cleanup() {
    local exit_code=$?
    echo ""
    echo "==> Tearing down stack…"
    ${COMPOSE} down -v --remove-orphans 2>/dev/null || true
    restore_env
    echo ""
    echo "Results: ${PASS} passed, ${FAIL} failed"
    if [[ $FAIL -gt 0 || $exit_code -ne 0 ]]; then
        exit 1
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------

echo ""
echo "==> Writing smoke docker/.env…"
write_smoke_env

echo "==> Building and starting unified brain stack…"
# `up -d` brings up tapps-brain-db → tapps-brain-migrate (one-shot) →
# tapps-brain-http → tapps-visual, respecting the depends_on health gates.
${COMPOSE} up -d --build

# ---------------------------------------------------------------------------
# Wait
# ---------------------------------------------------------------------------

echo ""
echo "==> Waiting for health probes…"

wait_for_url "http://localhost:${ADAPTER_PORT}/health" "tapps-brain-http /health" || {
    echo ""
    echo "ERROR: tapps-brain-http did not become healthy. Container logs:"
    ${COMPOSE} logs tapps-brain-http | tail -40
    exit 1
}

wait_for_url "http://localhost:${TAPPS_VISUAL_PORT}/" "tapps-visual dashboard" || {
    echo ""
    echo "ERROR: tapps-visual did not become ready. Container logs:"
    ${COMPOSE} logs tapps-visual | tail -20
    exit 1
}

# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

echo ""
echo "==> Running assertions…"

# tapps-brain-http direct endpoints
assert_http "http://localhost:${ADAPTER_PORT}/health"      "200" "GET /health → 200"
assert_http "http://localhost:${ADAPTER_PORT}/ready"       "200" "GET /ready → 200"
assert_http "http://localhost:${ADAPTER_PORT}/metrics"     "200" "GET /metrics → 200"
assert_http "http://localhost:${ADAPTER_PORT}/openapi.json" "200" "GET /openapi.json → 200"
assert_json_field_nonempty "http://localhost:${ADAPTER_PORT}/health" "status" "GET /health has .status"

# tapps-visual dashboard
assert_http "http://localhost:${TAPPS_VISUAL_PORT}/" "200" "GET dashboard / → 200"

# /snapshot proxied through nginx — 200 (with store), 503 (no store), or 401 (auth
# enabled, no token provided) are all OK — they prove nginx reached tapps-brain-http.
# 502 means the upstream hostname is wrong (regression guard for STORY-067.2).
SNAPSHOT_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    "http://localhost:${TAPPS_VISUAL_PORT}/snapshot" 2>/dev/null || echo "000")

if [[ "$SNAPSHOT_STATUS" == "200" || "$SNAPSHOT_STATUS" == "503" || "$SNAPSHOT_STATUS" == "401" ]]; then
    pass "GET /snapshot proxied through nginx (HTTP $SNAPSHOT_STATUS — adapter reachable)"
elif [[ "$SNAPSHOT_STATUS" == "502" ]]; then
    fail "GET /snapshot returned 502 — nginx cannot reach tapps-brain-http (upstream hostname mismatch?)"
else
    fail "GET /snapshot — unexpected HTTP $SNAPSHOT_STATUS"
fi
