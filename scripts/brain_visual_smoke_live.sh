#!/usr/bin/env bash
# scripts/brain_visual_smoke_live.sh — Visual dashboard smoke against a *running* stack.
#
# Asserts :8088/ HTML meta, :8088/snapshot via nginx proxy, and :8080/snapshot direct.
# Unlike scripts/hive_smoke.sh (boots an isolated stack), this probes the live deployment.
#
# Usage:
#   bash scripts/brain_visual_smoke_live.sh   # from repo root
#   make brain-visual-smoke-live              # via Makefile
#
# Auth: TAPPS_BRAIN_AUTH_TOKEN from the environment, else .env, else docker/.env.
# Override URLs:
#   TAPPS_VISUAL_BASE_URL=http://host:port   (default http://127.0.0.1:8088)
#   TAPPS_BRAIN_BASE_URL=http://host:port    (default http://127.0.0.1:8080)

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

export TAPPS_VISUAL_BASE_URL="${TAPPS_VISUAL_BASE_URL:-http://127.0.0.1:8088}"
export TAPPS_BRAIN_BASE_URL="${TAPPS_BRAIN_BASE_URL:-http://127.0.0.1:8080}"
export TAPPS_SMOKE_PROJECT_ID="${TAPPS_SMOKE_PROJECT_ID:-tapps-brain-smoke}"
export TAPPS_SMOKE_AGENT_ID="${TAPPS_SMOKE_AGENT_ID:-brain-visual-smoke-live}"
export TAPPS_SNAPSHOT_MAX_SECONDS="${TAPPS_SNAPSHOT_MAX_SECONDS:-30}"

echo "==> tapps-brain visual live smoke"
echo "    visual=${TAPPS_VISUAL_BASE_URL}"
echo "    brain=${TAPPS_BRAIN_BASE_URL}"
echo "    snapshot_slo=${TAPPS_SNAPSHOT_MAX_SECONDS}s"

exec python3 <<'PY'
import json
import os
import sys
import time
import urllib.error
import urllib.request

VISUAL = os.environ["TAPPS_VISUAL_BASE_URL"].rstrip("/")
BRAIN = os.environ["TAPPS_BRAIN_BASE_URL"].rstrip("/")
TOKEN = os.environ["TAPPS_BRAIN_AUTH_TOKEN"]
PROJECT = os.environ["TAPPS_SMOKE_PROJECT_ID"]
AGENT = os.environ["TAPPS_SMOKE_AGENT_ID"]
MAX_SNAPSHOT_SEC = float(os.environ.get("TAPPS_SNAPSHOT_MAX_SECONDS", "30"))

passed = 0
failed = 0
visual_root_ok = False


def ok(msg: str) -> None:
    global passed
    passed += 1
    print(f"  [PASS] {msg}")


def fail(msg: str) -> None:
    global failed
    failed += 1
    print(f"  [FAIL] {msg}")


def brain_down_hint(proxy_status: int, via: str) -> None:
    print("", file=sys.stderr)
    print(
        f"ERROR: tapps-visual is up (GET {VISUAL}/ → 200) but GET {via} failed (HTTP {proxy_status}).",
        file=sys.stderr,
    )
    print(
        "The nginx proxy cannot get a valid snapshot from tapps-brain-http.",
        file=sys.stderr,
    )
    print("  1. docker ps — verify tapps-brain-http is healthy", file=sys.stderr)
    print("  2. docker logs tapps-brain-http --tail 50", file=sys.stderr)
    print(
        f'  3. curl -sf -H "Authorization: Bearer $TAPPS_BRAIN_AUTH_TOKEN" "{BRAIN}/snapshot" | head',
        file=sys.stderr,
    )
    print("  4. make brain-smoke-live", file=sys.stderr)
    print("", file=sys.stderr)


def fetch(
    base: str,
    path: str,
    *,
    auth: bool = False,
    timeout: float = 35.0,
) -> tuple[int, str, float]:
    headers = {
        "X-Project-Id": PROJECT,
        "X-Agent-Id": AGENT,
    }
    if auth:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(f"{base}{path}", headers=headers, method="GET")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            elapsed = time.monotonic() - started
            return resp.status, raw, elapsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        elapsed = time.monotonic() - started
        return exc.code, raw, elapsed
    except TimeoutError:
        elapsed = time.monotonic() - started
        return 0, f"request timed out after {timeout:.0f}s", elapsed
    except OSError as exc:
        elapsed = time.monotonic() - started
        return 0, str(exc), elapsed


def validate_snapshot_body(
    status: int,
    raw: str,
    elapsed: float,
    label: str,
) -> dict | None:
    if status != 200:
        fail(f"{label} expected HTTP 200, got {status}: {raw[:200]}")
        return None
    if elapsed >= MAX_SNAPSHOT_SEC:
        fail(f"{label} took {elapsed:.2f}s (max {MAX_SNAPSHOT_SEC:.0f}s)")
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        fail(f"{label} response was not JSON: {raw[:200]}")
        return None
    schema_version = body.get("schema_version")
    if not isinstance(schema_version, int) or schema_version < 2:
        fail(f"{label} schema_version must be >= 2, got {schema_version!r}")
        return None
    fingerprint = body.get("fingerprint_sha256")
    if not isinstance(fingerprint, str) or not fingerprint:
        fail(f"{label} missing fingerprint_sha256")
        return None
    ok(
        f"{label} schema_version={schema_version} "
        f"fingerprint={fingerprint[:12]}… latency={elapsed:.2f}s"
    )
    # STORY-078.13: live retrieval panel fields
    mode = body.get("retrieval_effective_mode")
    if not isinstance(mode, str) or not mode:
        fail(f"{label} missing retrieval_effective_mode")
    else:
        ok(f"{label} retrieval_effective_mode={mode}")
    rm = body.get("retrieval_metrics")
    if not isinstance(rm, dict):
        fail(f"{label} missing retrieval_metrics object")
    else:
        for key in (
            "total_queries",
            "bm25_hits",
            "vector_hits",
            "rrf_fusions",
            "mean_latency_ms",
        ):
            if key not in rm:
                fail(f"{label} retrieval_metrics missing {key}")
        if all(k in rm for k in ("total_queries", "bm25_hits", "vector_hits", "rrf_fusions", "mean_latency_ms")):
            ok(f"{label} retrieval_metrics has all 5 counters")
    retrieval = body.get("retrieval")
    if retrieval is not None and not isinstance(retrieval, dict):
        fail(f"{label} retrieval must be object or null, got {type(retrieval).__name__}")
    elif isinstance(retrieval, dict):
        for key in ("latency_p50_ms", "latency_p95_ms", "latency_p99_ms", "latency_histogram"):
            if key not in retrieval:
                fail(f"{label} retrieval missing {key}")
        ok(f"{label} retrieval latency block present")
    vec_rows = body.get("vector_index_rows")
    if not isinstance(vec_rows, int) or vec_rows < 0:
        fail(f"{label} vector_index_rows must be non-negative int, got {vec_rows!r}")
    else:
        ok(f"{label} vector_index_rows={vec_rows}")
    return body


# --- Visual dashboard HTML ---
status, html, _ = fetch(VISUAL, "/", auth=False, timeout=15.0)
if status == 200 and "tapps-snapshot-url" in html:
    ok(f"GET {VISUAL}/ contains tapps-snapshot-url meta")
    visual_root_ok = True
elif status == 200:
    fail(f"GET {VISUAL}/ missing tapps-snapshot-url meta tag")
else:
    fail(f"GET {VISUAL}/ expected HTTP 200, got {status}: {html[:120]}")

# --- Visual nginx liveness (STORY-078.5) ---
hz_status, hz_raw, _ = fetch(VISUAL, "/healthz", auth=False, timeout=5.0)
if hz_status == 200:
    try:
        hz_body = json.loads(hz_raw) if hz_raw else {}
    except json.JSONDecodeError:
        fail(f"GET {VISUAL}/healthz response was not JSON: {hz_raw[:120]}")
        hz_body = {}
    if hz_body.get("ok") is True and hz_body.get("service") == "tapps-visual":
        ok(f"GET {VISUAL}/healthz ok service=tapps-visual")
    else:
        fail(f"GET {VISUAL}/healthz unexpected body: {hz_body!r}")
else:
    fail(f"GET {VISUAL}/healthz expected HTTP 200, got {hz_status}: {hz_raw[:120]}")

# --- Proxied snapshot via tapps-visual nginx ---
status, raw, elapsed = fetch(VISUAL, "/snapshot", auth=False, timeout=MAX_SNAPSHOT_SEC + 5)
if visual_root_ok and status != 200:
    brain_down_hint(status, f"{VISUAL}/snapshot")
validate_snapshot_body(status, raw, elapsed, f"GET {VISUAL}/snapshot")

# --- Direct snapshot on tapps-brain-http ---
status, raw, elapsed = fetch(BRAIN, "/snapshot", auth=True, timeout=MAX_SNAPSHOT_SEC + 5)
if status == 0 and visual_root_ok:
    print("", file=sys.stderr)
    print(
        f"ERROR: tapps-brain-http did not respond to GET {BRAIN}/snapshot within "
        f"{MAX_SNAPSHOT_SEC:.0f}s.",
        file=sys.stderr,
    )
    print("  1. docker ps — verify tapps-brain-http is healthy", file=sys.stderr)
    print("  2. docker logs tapps-brain-http --tail 50", file=sys.stderr)
    print("  3. make brain-smoke-live", file=sys.stderr)
    print("", file=sys.stderr)
validate_snapshot_body(status, raw, elapsed, f"GET {BRAIN}/snapshot")

print("")
print(f"Results: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
