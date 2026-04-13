#!/usr/bin/env bash
# scripts/hive_smoke.sh — End-to-end smoke test for the Docker hive stack.
# (EPIC-067 STORY-067.5)
#
# Boots the full compose stack using test credentials, waits for health probes,
# asserts all endpoints return correct responses, then tears the stack down.
#
# Usage:
#   bash scripts/hive_smoke.sh     # from repo root
#   make hive-smoke                # via Makefile target
#
# The script temporarily writes non-default values to docker/secrets/ for the
# duration of the run and restores them on exit. This bypasses the credential
# guard intentionally — smoke tests are not production deployments.

set -euo pipefail

COMPOSE_FILE="docker/docker-compose.hive.yaml"
COMPOSE="docker compose -f ${COMPOSE_FILE}"

# Use non-default ports to avoid clashing with a running live stack.
export TAPPS_VISUAL_PORT="${TAPPS_VISUAL_PORT:-18088}"
ADAPTER_PORT="${TAPPS_BRAIN_HTTP_PORT:-18080}"

# Non-default credentials for this smoke run.
SMOKE_PASSWORD="smoke-$(openssl rand -hex 8 2>/dev/null || echo testonly)"
SMOKE_TOKEN="smoke-$(openssl rand -hex 8 2>/dev/null || echo testonly)"

MAX_WAIT=90   # seconds to wait for each health probe
PASS=0
FAIL=0

echo "==> tapps-brain hive smoke test"
echo "    Visual port  : ${TAPPS_VISUAL_PORT}"
echo "    Adapter port : ${ADAPTER_PORT}"

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
# Secret file management — write smoke credentials, restore on exit
# ---------------------------------------------------------------------------

PW_FILE="docker/secrets/tapps_hive_password.txt"
TOKEN_FILE="docker/secrets/tapps_http_auth_token.txt"

PW_BACKUP=""
TOKEN_BACKUP=""
if [[ -f "$PW_FILE" ]]; then
    PW_BACKUP=$(cat "$PW_FILE")
fi
if [[ -f "$TOKEN_FILE" ]]; then
    TOKEN_BACKUP=$(cat "$TOKEN_FILE")
fi

restore_secrets() {
    if [[ -n "$PW_BACKUP" ]]; then
        echo "$PW_BACKUP" > "$PW_FILE"
    fi
    if [[ -n "$TOKEN_BACKUP" ]]; then
        echo "$TOKEN_BACKUP" > "$TOKEN_FILE"
    fi
}

write_smoke_secrets() {
    mkdir -p docker/secrets
    echo "$SMOKE_PASSWORD" > "$PW_FILE"
    echo "$SMOKE_TOKEN"   > "$TOKEN_FILE"
}

# ---------------------------------------------------------------------------
# Cleanup: tear down stack, restore secrets, print results
# ---------------------------------------------------------------------------

cleanup() {
    local exit_code=$?
    echo ""
    echo "==> Tearing down stack…"
    TAPPS_HIVE_PASSWORD="${SMOKE_PASSWORD}" \
    TAPPS_HTTP_PORT="${ADAPTER_PORT}" \
        ${COMPOSE} down -v --remove-orphans 2>/dev/null || true
    restore_secrets
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
echo "==> Writing smoke credentials…"
write_smoke_secrets

echo "==> Building and starting hive stack…"
# Boot the DB first, run migrations, then start the HTTP adapter and visual.
TAPPS_HIVE_PASSWORD="${SMOKE_PASSWORD}" \
TAPPS_HTTP_PORT="${ADAPTER_PORT}" \
    ${COMPOSE} up -d tapps-hive-db

echo "==> Running hive migrations…"
TAPPS_HIVE_PASSWORD="${SMOKE_PASSWORD}" \
TAPPS_HTTP_PORT="${ADAPTER_PORT}" \
    ${COMPOSE} run --rm tapps-hive-migrate

TAPPS_HIVE_PASSWORD="${SMOKE_PASSWORD}" \
TAPPS_HTTP_PORT="${ADAPTER_PORT}" \
    ${COMPOSE} up -d tapps-brain-http tapps-visual

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
