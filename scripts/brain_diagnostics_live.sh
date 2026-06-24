#!/usr/bin/env bash
# scripts/brain_diagnostics_live.sh — operator diagnostics against a *running* hive stack.
#
# Read-only: /healthz (deep), /snapshot summary, stale GC preview, diagnostics report.
# Targets the deployed tapps-brain-http container + Postgres (project tapps-brain).
#
# Usage:
#   bash scripts/brain_diagnostics_live.sh
#   make brain-diagnostics-live
#
# Overrides:
#   TAPPS_BRAIN_HTTP_CONTAINER   default tapps-brain-http
#   TAPPS_BRAIN_PROJECT          default: busiest http-adapter project_id in Postgres
#   TAPPS_BRAIN_AGENT_ID         default http-adapter
#   TAPPS_BRAIN_VISUAL_URL       default http://127.0.0.1:8088
#   TAPPS_BRAIN_BASE_URL         default http://127.0.0.1:8080

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

HTTP_CONTAINER="${TAPPS_BRAIN_HTTP_CONTAINER:-tapps-brain-http}"
AGENT_ID="${TAPPS_BRAIN_AGENT_ID:-http-adapter}"
VISUAL_URL="${TAPPS_BRAIN_VISUAL_URL:-http://127.0.0.1:8088}"
BASE_URL="${TAPPS_BRAIN_BASE_URL:-http://127.0.0.1:8080}"
PROJECT_DIR_IN_CONTAINER="${TAPPS_BRAIN_PROJECT_DIR:-/var/lib/tapps-brain}"

if ! docker inspect "$HTTP_CONTAINER" >/dev/null 2>&1; then
    echo "ERROR: container '$HTTP_CONTAINER' not running." >&2
    exit 1
fi

if [[ -z "${TAPPS_BRAIN_PROJECT:-}" ]]; then
    TAPPS_BRAIN_PROJECT="$(
        docker exec tapps-brain-db psql -U tapps -d tapps_brain -t -A -c \
            "SELECT project_id FROM private_memories WHERE agent_id = '${AGENT_ID}' \
             GROUP BY project_id ORDER BY COUNT(*) DESC LIMIT 1" 2>/dev/null | tr -d '[:space:]'
    )"
fi

if [[ -z "${TAPPS_BRAIN_PROJECT:-}" ]]; then
    echo "ERROR: could not resolve TAPPS_BRAIN_PROJECT (set env or check Postgres)." >&2
    exit 1
fi

echo "==> tapps-brain live diagnostics"
echo "    container=${HTTP_CONTAINER}"
echo "    project_id=${TAPPS_BRAIN_PROJECT}"
echo "    agent_id=${AGENT_ID}"
echo "    base=${BASE_URL}"
echo ""

echo "--- GET /healthz?deep=1 ---"
curl -sf "${BASE_URL}/healthz?deep=1" | python3 -m json.tool
echo ""

echo "--- GET ${VISUAL_URL}/snapshot (cached aggregate) ---"
curl -sf "${VISUAL_URL}/snapshot" -o /tmp/tapps-brain-snapshot-live.json
python3 <<'PY'
import json
with open("/tmp/tapps-brain-snapshot-live.json") as f:
    d = json.load(f)
h = d.get("health") or {}
diag = d.get("diagnostics") or {}
warns = [x for x in (d.get("scorecard") or []) if x.get("status") == "warn"]
print(f"entries={h.get('entry_count')} gc_candidates={h.get('gc_candidates')}")
print(f"composite={diag.get('composite_score')} circuit={diag.get('circuit_state')}")
print(f"scorecard_warnings={len(warns)}")
for w in warns:
    print(f"  warn: {w.get('title')}: {w.get('detail')}")
PY
echo ""

echo "--- maintenance stale (in-container) ---"
docker exec "$HTTP_CONTAINER" bash -lc "
  export TAPPS_BRAIN_PROJECT='${TAPPS_BRAIN_PROJECT}'
  export TAPPS_BRAIN_EMBEDDING_REQUIRED=0
  tapps-brain --agent-id '${AGENT_ID}' maintenance stale --project-dir '${PROJECT_DIR_IN_CONTAINER}' 2>/dev/null | head -20
"
echo ""

echo "--- diagnostics report (in-container) ---"
docker exec "$HTTP_CONTAINER" bash -lc "
  export TAPPS_BRAIN_PROJECT='${TAPPS_BRAIN_PROJECT}'
  export TAPPS_BRAIN_EMBEDDING_REQUIRED=0
  tapps-brain --agent-id '${AGENT_ID}' diagnostics report \
    --project-dir '${PROJECT_DIR_IN_CONTAINER}' --no-record-history 2>/dev/null \
    | grep -E 'Status:|Composite|Hive composite|Gap signals|freshness|staleness|integrity|retrieval'
"
