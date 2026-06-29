#!/usr/bin/env bash
# scripts/brain_smoke_live.sh — HTTP smoke test against a *running* tapps-brain stack.
#
# Unlike scripts/hive_smoke.sh (which boots an isolated compose project on
# alternate ports and tears it down), this script probes the live deployment —
# by default http://127.0.0.1:8080 from `docker/docker-compose.hive.yaml`.
#
# Usage:
#   bash scripts/brain_smoke_live.sh          # from repo root
#   make brain-smoke-live                     # via Makefile
#
# Auth: TAPPS_BRAIN_AUTH_TOKEN from the environment, else .env, else docker/.env.
# Override base URL: TAPPS_BRAIN_BASE_URL=http://host:port

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -z "${TAPPS_BRAIN_AUTH_TOKEN:-}" ]]; then
    if [[ -f .env ]]; then
        set -o allexport
        # shellcheck disable=SC1091
        source .env
        set +o allexport
    elif [[ -f docker/.env ]]; then
        set -o allexport
        # shellcheck disable=SC1091
        source docker/.env
        set +o allexport
    fi
fi

if [[ -z "${TAPPS_BRAIN_AUTH_TOKEN:-}" ]]; then
    echo "ERROR: TAPPS_BRAIN_AUTH_TOKEN is not set (.env or docker/.env)." >&2
    exit 1
fi

# TAP-4465: name the smoke tenant with a reserved prefix (smoke-) so leaked
# rows are recoverable via `make purge-test-tenants` and the integration
# session-end sweep.
export TAPPS_BRAIN_BASE_URL="${TAPPS_BRAIN_BASE_URL:-http://127.0.0.1:8080}"
export TAPPS_SMOKE_PROJECT_ID="${TAPPS_SMOKE_PROJECT_ID:-smoke-brain-live}"
export TAPPS_SMOKE_AGENT_ID="${TAPPS_SMOKE_AGENT_ID:-brain-smoke-live}"

EXPECTED_VERSION="$(
    grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/'
)"

echo "==> tapps-brain live smoke"
echo "    base=${TAPPS_BRAIN_BASE_URL}"
echo "    project=${TAPPS_SMOKE_PROJECT_ID}"
echo "    expect_version=${EXPECTED_VERSION}"

set +e
python3 - "$EXPECTED_VERSION" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

EXPECTED_VERSION = sys.argv[1]
BASE = os.environ["TAPPS_BRAIN_BASE_URL"].rstrip("/")
TOKEN = os.environ["TAPPS_BRAIN_AUTH_TOKEN"]
PROJECT = os.environ["TAPPS_SMOKE_PROJECT_ID"]
AGENT = os.environ["TAPPS_SMOKE_AGENT_ID"]
FILE_PATH = "src/tapps_brain/services/kg_service.py"

passed = 0
failed = 0


def ok(msg: str) -> None:
    global passed
    passed += 1
    print(f"  [PASS] {msg}")


def fail(msg: str) -> None:
    global failed
    failed += 1
    print(f"  [FAIL] {msg}")


def request(method: str, path: str, body: dict | None = None, *, auth: bool = True) -> tuple[int, dict]:
    headers = {
        "X-Project-Id": PROJECT,
        "X-Agent-Id": AGENT,
        "Content-Type": "application/json",
    }
    if auth:
        headers["Authorization"] = f"Bearer {TOKEN}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw) if raw else {"detail": exc.reason}
        except json.JSONDecodeError:
            payload = {"detail": raw or exc.reason}
        return exc.code, payload


status, health = request("GET", "/healthz", auth=False)
if status == 200 and health.get("ok") and health.get("brain_version") == EXPECTED_VERSION:
    ok(f"GET /healthz version={health['brain_version']} db_ok={health.get('db_ok')}")
else:
    fail(f"GET /healthz expected 200 ok + {EXPECTED_VERSION}, got {status} {health}")

status, ready = request("GET", "/ready", auth=False)
if status == 200 and ready.get("status") == "ready":
    ok(f"GET /ready status=ready migration_version={ready.get('migration_version')}")
else:
    fail(f"GET /ready expected 200 status=ready, got {status} {ready}")

payload = {
    "event_type": "quality_metric",
    "payload": {
        "score": 93.5,
        "duration_ms": 501,
        "gate_passed": True,
        "started_at": "2026-06-09T14:25:00Z",
        "file_path": FILE_PATH,
    },
    "entities": [{"type": "file", "id": FILE_PATH}],
}
status, recorded = request("POST", "/v1/experience", payload)
event_id = recorded.get("event_id")
if status == 200 and event_id:
    ok(f"POST /v1/experience event_id={event_id[:8]}…")
else:
    fail(f"POST /v1/experience expected 200 + event_id, got {status} {recorded}")

status, queried = request(
    "POST",
    "/v1/experience:query",
    {"event_type": "quality_metric", "entity_id": FILE_PATH, "limit": 10},
)
events = queried.get("events") or []
match = next((e for e in events if e.get("event_id") == event_id), None)
if status == 200 and match and match.get("payload", {}).get("score") == 93.5:
    ok(
        f"POST /v1/experience:query round-trip score=93.5 "
        f"count={queried.get('count')}"
    )
else:
    fail(f"POST /v1/experience:query round-trip failed: {status} {queried}")

status, bad = request("POST", "/v1/experience:query", {"limit": 5})
if status == 400:
    ok("POST /v1/experience:query rejects missing event_type (400)")
else:
    fail(f"POST /v1/experience:query missing event_type should 400, got {status} {bad}")

import time

MAX_SNAPSHOT_SEC = float(os.environ.get("TAPPS_SNAPSHOT_MAX_SECONDS", "30"))


def request_timed(method: str, path: str, *, auth: bool = True) -> tuple[int, dict, float]:
    headers = {
        "X-Project-Id": PROJECT,
        "X-Agent-Id": AGENT,
        "Content-Type": "application/json",
    }
    if auth:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(f"{BASE}{path}", headers=headers, method=method)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=MAX_SNAPSHOT_SEC + 5) as resp:
            raw = resp.read().decode()
            elapsed = time.monotonic() - started
            return resp.status, json.loads(raw) if raw else {}, elapsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        elapsed = time.monotonic() - started
        try:
            payload = json.loads(raw) if raw else {"detail": exc.reason}
        except json.JSONDecodeError:
            payload = {"detail": raw or exc.reason}
        return exc.code, payload, elapsed
    except TimeoutError:
        elapsed = time.monotonic() - started
        return 0, {"detail": f"timed out after {MAX_SNAPSHOT_SEC + 5:.0f}s"}, elapsed
    except OSError as exc:
        elapsed = time.monotonic() - started
        return 0, {"detail": str(exc)}, elapsed


status, snapshot, snap_elapsed = request_timed("GET", "/snapshot")
schema_version = snapshot.get("schema_version")
fingerprint = snapshot.get("fingerprint_sha256")
if status == 0:
    fail(
        f"GET /snapshot timed out or was unreachable after {snap_elapsed:.2f}s "
        f"(max {MAX_SNAPSHOT_SEC:.0f}s)"
    )
elif (
    status == 200
    and isinstance(schema_version, int)
    and schema_version >= 2
    and isinstance(fingerprint, str)
    and fingerprint
    and snap_elapsed < MAX_SNAPSHOT_SEC
):
    ok(
        f"GET /snapshot schema_version={schema_version} "
        f"fingerprint={fingerprint[:12]}… latency={snap_elapsed:.2f}s"
    )
else:
    fail(
        f"GET /snapshot expected 200 + schema_version>=2 + fingerprint_sha256 "
        f"within {MAX_SNAPSHOT_SEC:.0f}s, got status={status} "
        f"schema_version={schema_version!r} latency={snap_elapsed:.2f}s {snapshot}"
    )

print("")
print(f"Results: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
SMOKE_RC=$?
set -e

# TAP-4465: best-effort cleanup of the smoke tenant's rows so repeated live
# smokes do not accumulate residue. The smoke writes only experience_events;
# delete them inside the db container. Never let cleanup change the smoke
# result — guarded so failures (no docker, container down) are non-fatal.
purge_smoke_tenant() {
    command -v docker >/dev/null 2>&1 || return 0
    local db_container="${TAPPS_BRAIN_DB_CONTAINER:-tapps-brain-db}"
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$db_container" || return 0
    local pg_user="${POSTGRES_USER:-tapps}"
    local pg_db="${POSTGRES_DB:-tapps_brain}"
    docker exec "$db_container" psql -U "$pg_user" -d "$pg_db" -v ON_ERROR_STOP=0 \
        -c "DELETE FROM experience_events WHERE project_id = '${TAPPS_SMOKE_PROJECT_ID}';" \
        >/dev/null 2>&1 \
        && echo "    cleaned up smoke tenant ${TAPPS_SMOKE_PROJECT_ID}" \
        || echo "    (smoke tenant cleanup skipped — purge later via 'make purge-test-tenants')"
}
purge_smoke_tenant || true

exit "$SMOKE_RC"
