#!/usr/bin/env bash
# scripts/brain_diagnostics_live.sh — operator diagnostics against a *running* hive stack.
#
# Read-only by default: /healthz (deep), /snapshot summary, stale GC preview,
# diagnostics JSON report. Set AUTO_GC=1 to archive stale candidates in-container.
#
# Usage:
#   bash scripts/brain_diagnostics_live.sh
#   make brain-diagnostics-live
#   AUTO_GC=1 make brain-diagnostics-live   # archive stale candidates when present
#
# Overrides:
#   TAPPS_BRAIN_HTTP_CONTAINER   default tapps-brain-http
#   BRAIN_LIVE_PROJECT           optional project_id (default: busiest Postgres row for agent)
#   BRAIN_LIVE_AGENT_ID          default http-adapter (ignores host TAPPS_BRAIN_AGENT_ID)
#   BRAIN_LIVE_PROJECT_DIR       default /var/lib/tapps-brain in container
#   TAPPS_BRAIN_VISUAL_URL       default http://127.0.0.1:8088
#   TAPPS_BRAIN_BASE_URL         default http://127.0.0.1:8080
#   AUTO_GC                      default 0 — set to 1 to run maintenance gc when stale > 0

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

HTTP_CONTAINER="${TAPPS_BRAIN_HTTP_CONTAINER:-tapps-brain-http}"
# Live-stack identity — do NOT inherit host TAPPS_BRAIN_* (dev repo / local agent).
AGENT_ID="${BRAIN_LIVE_AGENT_ID:-http-adapter}"
VISUAL_URL="${TAPPS_BRAIN_VISUAL_URL:-http://127.0.0.1:8088}"
BASE_URL="${TAPPS_BRAIN_BASE_URL:-http://127.0.0.1:8080}"
PROJECT_DIR_IN_CONTAINER="${BRAIN_LIVE_PROJECT_DIR:-/var/lib/tapps-brain}"
AUTO_GC="${AUTO_GC:-0}"

if ! docker inspect "$HTTP_CONTAINER" >/dev/null 2>&1; then
    echo "ERROR: container '$HTTP_CONTAINER' not running." >&2
    exit 1
fi

if [[ -n "${BRAIN_LIVE_PROJECT:-}" ]]; then
    LIVE_PROJECT="$BRAIN_LIVE_PROJECT"
else
    LIVE_PROJECT="$(
        docker exec tapps-brain-db psql -U tapps -d tapps_brain -t -A -c \
            "SELECT project_id FROM private_memories WHERE agent_id = '${AGENT_ID}' \
             GROUP BY project_id ORDER BY COUNT(*) DESC LIMIT 1" 2>/dev/null | tr -d '[:space:]'
    )"
fi

if [[ -z "${LIVE_PROJECT:-}" ]]; then
    echo "ERROR: could not resolve live project_id (set BRAIN_LIVE_PROJECT or check Postgres)." >&2
    exit 1
fi

echo "==> tapps-brain live diagnostics"
echo "    container=${HTTP_CONTAINER}"
echo "    project_id=${LIVE_PROJECT}"
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
STALE_TMP="/tmp/tapps-brain-stale-$$.json"
docker exec "$HTTP_CONTAINER" bash -lc "
  export TAPPS_BRAIN_PROJECT='${LIVE_PROJECT}'
  export TAPPS_BRAIN_EMBEDDING_REQUIRED=0
  tapps-brain --agent-id '${AGENT_ID}' maintenance stale \
    --project-dir '${PROJECT_DIR_IN_CONTAINER}' --json 2>/dev/null
" >"$STALE_TMP"
STALE_COUNT="$(python3 -c "import json; print(json.load(open('$STALE_TMP')).get('count', 0))")"
echo "stale_candidates=${STALE_COUNT}"
if [[ "$STALE_COUNT" -gt 0 ]]; then
    python3 <<PY
import json, collections
with open("$STALE_TMP") as f:
    data = json.load(f)
reasons = collections.Counter()
for e in data.get("entries", []):
    for r in e.get("reasons", []):
        reasons[r] += 1
for r, n in reasons.most_common():
    print(f"  reason {r}: {n}")
samples = [e.get("key") for e in data.get("entries", [])[:5]]
print("  sample keys:", samples)
PY
    if [[ "$AUTO_GC" == "1" ]]; then
        echo ""
        echo "--- AUTO_GC: running maintenance gc ---"
        docker exec "$HTTP_CONTAINER" bash -lc "
          export TAPPS_BRAIN_PROJECT='${LIVE_PROJECT}'
          export TAPPS_BRAIN_EMBEDDING_REQUIRED=0
          tapps-brain --agent-id '${AGENT_ID}' maintenance gc \
            --project-dir '${PROJECT_DIR_IN_CONTAINER}' --json 2>/dev/null \
            | python3 -m json.tool
        "
    else
        echo ""
        echo "Hint: archive stale entries with:"
        echo "  AUTO_GC=1 make brain-diagnostics-live"
        echo "  # or: docker exec ${HTTP_CONTAINER} tapps-brain --agent-id ${AGENT_ID} maintenance gc ..."
    fi
else
    echo "  (none)"
fi
echo ""

echo "--- diagnostics report (JSON, in-container) ---"
DIAG_TMP="/tmp/tapps-brain-diag-$$.json"
docker exec "$HTTP_CONTAINER" bash -lc "
  export TAPPS_BRAIN_PROJECT='${LIVE_PROJECT}'
  export TAPPS_BRAIN_EMBEDDING_REQUIRED=0
  tapps-brain --agent-id '${AGENT_ID}' diagnostics report \
    --project-dir '${PROJECT_DIR_IN_CONTAINER}' --no-record-history --json 2>/dev/null
" >"$DIAG_TMP"
python3 <<PY
import json

def grade(score: float) -> str:
    if score >= 0.85:
        return "A"
    if score >= 0.70:
        return "B"
    if score >= 0.55:
        return "C"
    if score >= 0.40:
        return "D"
    return "F"

with open("$DIAG_TMP") as f:
    rep = json.load(f)
print(f"composite={rep['composite_score']:.4f} circuit={rep['circuit_state']}")
if rep.get("hive_composite_score") is not None:
    print(f"hive_composite={rep['hive_composite_score']:.4f}")
print(f"gap_count={rep.get('gap_count', 0)}")
for name, dim in sorted(rep.get("dimensions", {}).items()):
    s = float(dim["score"])
    print(f"  {name}: {s:.4f} ({grade(s)})")
fr = rep.get("dimensions", {}).get("freshness", {})
raw = fr.get("raw_details") or {}
if raw.get("context_share") is not None:
    print(
        f"  freshness context_share={raw['context_share']} "
        f"tier_avg_age_days={raw.get('tier_avg_age_days', {})}"
    )
    if float(raw["context_share"]) >= 0.7:
        print(
            "  note: freshness grade F is expected when ~80% of entries are "
            "short-lived context tier near the 14d half-life."
        )
PY
rm -f "$STALE_TMP" "$DIAG_TMP"
